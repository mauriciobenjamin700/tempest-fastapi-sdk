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

## Widget catalog

Widgets come from `tempest_core`. For SSR you use a handful of them as
building blocks; **all** accept `tag=`, `attrs=`, `style=` and `key=`.

| Widget | Renders | Use |
|--------|---------|-----|
| `Text(content=...)` | `<span>` (or the `tag`) with **escaped** text | Any text: heading, paragraph, `<option>`, `<label>` |
| `Column(children=[...])` | `<div>` with `display:flex; flex-direction:column` | Stack vertically |
| `Row(children=[...])` | `<div>` with `display:flex` (row) | Align horizontally |
| `Container(child=...)` | **plain** `<div>` (no flex), one child | Semantic wrapper (`tag="section"`, `tag="article"`) |
| `Button(label=...)` | styled `<button>` | Actions (via HTMX `attrs` — see below) |
| `Spacer()` | flexible space | Push items apart in a `Row`/`Column` |

```python
from tempest_core import Column, Container, Row, Spacer, Text

Container(
    tag="section",
    child=Row(
        children=[
            Text(content="Title", tag="h2"),
            Spacer(),
            Text(content="v1.0", tag="small"),
        ],
    ),
)
# <section><div style="display: flex"><h2>Title</h2>…<small>v1.0</small></div></section>
```

!!! warning "`Button.on_click` is ignored in SSR"
    `on_click` is a runtime handler (WASM/server); it does **not** run in
    static HTML. For SSR interactivity, use `attrs` with HTMX (`hx-post`,
    `hx-get`, …) — see [HTMX](#htmx-served-locally-no-cdn).

!!! tip "`tag` + `attrs` are the universal escape hatch"
    There is no dedicated widget for every HTML tag — and there needn't be.
    Any element comes out of a container widget with `tag=` and `attrs=`:
    `Text(content="", tag="input", attrs={"name": "email", "type": "email"})`
    renders `<input name="email" type="email" />`. For `hx-*`/`aria-*`/`data-*`
    there are typed builders — see [Typed attributes](#typed-attributes-htmx-aria-data).

## Typed styling with `Style`

Instead of loose CSS, each widget accepts a typed `Style` the renderer turns
into inline CSS. Spacing uses `Edge`.

```python
from tempest_core import Column, Style, Text
from tempest_core.style import Edge

Column(
    style=Style(gap=12.0, padding=Edge.all(16)),
    children=[Text(content="Card", tag="h3")],
)
# <div style="display: flex; flex-direction: column; gap: 12px; padding: 16px 16px 16px 16px">…
```

- `Edge.all(16)` / `Edge.symmetric(vertical=8, horizontal=16)` /
  `Edge.only(top=4)` — typed margins and paddings.
- `gap`, `padding`, `margin`, colors and typography land in the inline
  `style=""`.
- The `Style → CSS` conversion is **byte-identical** between the Python
  renderer (SSR) and the JS client (WASM/server) — the same screen on both
  sides.

!!! info "Prefer classes/`attrs` for external stylesheets"
    For real CSS (external sheets, media queries), add
    `attrs={"class": "card"}` and serve your `.css` as static. Inline
    `Style` is great for local layout and self-contained components.

## Reusable components

`Page` is a `Component`. You can extract **any** subtree into a typed
`Component` and reuse it — the page stays declarative and testable in pieces.

```python
from tempest_core import Column, Text, Widget
from tempest_core.widgets import Component

from tempest_fastapi_sdk.ssr import Page, html_response


class Card(Component):
    """A reusable card with a heading + body."""

    heading: str
    body_text: str

    def render(self) -> Widget:
        return Column(
            tag="section",
            attrs={"class": "card"},
            children=[
                Text(content=self.heading, tag="h3"),
                Text(content=self.body_text, tag="p"),
            ],
        )


class HomePage(Page):
    def body(self) -> Widget:
        return Column(
            tag="main",
            children=[
                Card(heading="Sales", body_text="$12,400 today"),
                Card(heading="Users", body_text="312 active"),
            ],
        )
```

In a `Component` you override **`render()`** (not `body()`/`shell()` — those
belong to `Page`). The renderer expands each `Component` through its
`render()`, recursively.

## Forms and inputs

There is no dedicated form widget — you compose it with `tag`/`attrs` and
receive the POST with FastAPI's `Form`, like any route.

```python
from tempest_core import Button, Column, Text, Widget
from fastapi import FastAPI, Form

from tempest_fastapi_sdk.ssr import Page, html_response

app: FastAPI = FastAPI()


class SignupPage(Page):
    def body(self) -> Widget:
        return Column(
            tag="form",
            attrs={"method": "post", "action": "/signup"},
            children=[
                Text(content="", tag="input",
                     attrs={"name": "email", "type": "email", "required": "required"}),
                Text(content="", tag="input",
                     attrs={"name": "password", "type": "password", "required": "required"}),
                Button(label="Create account", attrs={"type": "submit"}),
            ],
        )


@app.get("/signup")
def signup_form() -> object:
    return html_response(SignupPage(title="Sign up"), title="Sign up")


@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...)) -> object:
    # ... create the user via an SDK Service/Repository ...
    return html_response(
        Text(content=f"Account created for {email}", tag="p"), document=False
    )
```

A `<select>` comes out the same way: a `Column(tag="select", ...)` with
`Text(tag="option", attrs={"value": ...})` as children.

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
    `&lt;script&gt;` in the final HTML — no accidental injection. This
    applies to `content` and to the values in `attrs` — never build HTML by
    string concatenation; let the widgets escape.

### HTMX patterns you'll reuse

HTMX reads `hx-*` attributes from the HTML and does the AJAX for you. The
ones that show up most in SSR pages:

| Attribute | What it does |
|-----------|--------------|
| `hx-get` / `hx-post` / `hx-put` / `hx-delete` | Fires the request with that method |
| `hx-target` | CSS selector of the element receiving the response (`#id`, `closest li`) |
| `hx-swap` | How to apply it: `outerHTML`, `innerHTML`, `beforeend` (append), `delete` |
| `hx-trigger` | What triggers it: `click` (default), `submit`, `keyup changed delay:300ms` |
| `hx-confirm` | Shows a `confirm()` before sending |
| `hx-indicator` | Selector of a spinner shown during the request |
| `hx-on::after-request` | Inline JS on an HTMX event (e.g. `this.reset()` after submit) |

The golden rule: the route returns **a fragment** (`document=False`) and
HTMX slots it in via `hx-target` + `hx-swap`. An empty fragment
(`Text(content="", tag="span")`) with `hx-swap="outerHTML"` **removes** the
element — that's how "delete" works.

!!! tip "Appending to a list"
    `hx-target="#list"` + `hx-swap="beforeend"` on a `<form>` makes each
    submit **append** the returned `<li>`, without reloading the rest.

## Typed attributes: `htmx()`, `aria()`, `data()`

`attrs` is `dict[str, str]` because the HTML attribute space is open — but
typing `{"hx-post": ..., "hx-target": ..., "hx-swap": ...}` by hand is
typo-prone and has no autocomplete. The SDK ships **typed builders** that
assemble that dict from keyword arguments. No magic: the return value is
**exactly** the dict you'd write by hand — inspectable and mergeable.

```python
from tempest_fastapi_sdk.ssr import aria, data, htmx

htmx(post="/tasks", target="#tasks", swap="beforeend")
# {"hx-post": "/tasks", "hx-target": "#tasks", "hx-swap": "beforeend"}

aria(label="Close", role="button", expanded=False)
# {"aria-label": "Close", "role": "button", "aria-expanded": "false"}

data(user_id="42", active=True)
# {"data-user-id": "42", "data-active": "true"}
```

Before (stringly-typed) and after (clear and typed):

```python
# before
Button(label="Save", attrs={"hx-post": "/save", "hx-swap": "outerHTML"})

# after
from tempest_fastapi_sdk.ssr import htmx
Button(label="Save", attrs=htmx(post="/save", swap="outerHTML"))
```

Each builder returns a `dict[str, str]`, so you merge freely with other
builders and raw keys:

```python
Row(
    tag="li",
    attrs={**htmx(delete="/tasks/1", swap="outerHTML"), **aria(label="Delete"), "id": "task-1"},
)
```

- **`htmx(...)`** — `get`/`post`/`put`/`patch`/`delete` (URLs), `target`,
  `swap`, `trigger`, `confirm`, `indicator`, `push_url`, `boost`, … Booleans
  render as `"true"`/`"false"`; `vals`/`headers` accept a dict and are
  **JSON-encoded** for you; `on={":after-request": "this.reset()"}` becomes
  `hx-on::after-request`.
- **`aria(...)`** — `label`/`role`/`hidden`/`expanded`/`live`/… → `aria-*`
  (and the bare `role`). Accessibility without memorizing the names.
- **`data(...)`** — kwargs → `data-*` (underscore becomes hyphen: `user_id`
  → `data-user-id`).

!!! check "From 'magic' to clarity"
    The base type stays `dict[str, str]` (the HTML boundary is open by
    nature), but the **call site becomes typed**: autocomplete, static
    checking, no silent typo in `hx-post`. You write typed code that still
    flows into `attrs` — nothing is hidden, nothing becomes magic.

## Testing SSR pages

An SSR page is just a route that returns HTML — test it with `TestClient`
and assert the pieces that matter. Fast, no browser:

```python
from fastapi.testclient import TestClient

from main import app


def test_home_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
    assert "<title>Home</title>" in response.text
    assert "Hello, Ana!" in response.text


def test_increment_returns_fragment() -> None:
    with TestClient(app) as client:
        fragment = client.post("/increment")
    # Fragment: no <!doctype>, just the swapped piece.
    assert "<!doctype" not in fragment.text.lower()
    assert 'id="counter"' in fragment.text
```

!!! tip "Render without HTTP"
    For a pure unit test, call the renderer directly:
    `from tempestweb.html import render_to_html; html = render_to_html(MyPage(title="x").render())`.

## Which approach to use

The SDK covers the whole spectrum from "HTML on the server" to "SPA in the
browser". Pick by scenario:

| You want… | Use | Cost |
|-----------|-----|------|
| Server-rendered page, SEO, little interaction | **SSR** (`Page` + `html_response`) | No build; HTML per request |
| Interaction without an SPA or hand-written JS | **SSR + HTMX** (`make_htmx_router`) | No build; partial swaps |
| A rich app that runs offline in the browser | **WASM SPA** (`make_web_app_router`) | `tempestweb build --mode wasm` |
| Server-driven reactive UI, instant boot | **Server-mode** (`build_web_app`) | `tempestweb build --mode server` |

The last three are complete, runnable projects in
[Fullstack web](fullstack-web.md). For the **frontend calling the SDK
backend** (typed HTTP, idempotency, retry), see the recipe
[tempestweb frontend + SDK backend](recipes/tempestweb-frontend.md).

## Serve a compiled tempestweb build

The sections above render pages **per request**. If instead you compiled
a frontend with `tempestweb build`, the SDK hosts the finished artifact —
it only serves the `dist/`, it does not build (that stays in the
tempestweb CLI/CI flow). There are two artifacts, each with the shape
that fits it:

| Artifact | What it is | How to serve |
|----------|------------|--------------|
| `dist/wasm` | **Static** SPA (Pyodide runs in the browser: `index.html` + `bootstrap.js` + wasm + service worker) | `make_web_app_router` → `APIRouter` |
| `dist/server` | **Live** app over WebSocket/SSE (tempestweb server engine) | `build_web_app` → `FastAPI` (sub-app to mount) |

`detect_build_mode(dir)` tells which it is (`"wasm"` or `"server"`).

### Static SPA (`make_web_app_router`)

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import make_web_app_router

app = FastAPI()

# ... include your API routers FIRST ...
# app.include_router(api_router)

# ... and the frontend router LAST so specific routes win:
app.include_router(make_web_app_router("dist/wasm"))
```

The router serves every file in the build and, for any unmatched path,
falls back to `index.html` (single-page history fallback — a hard refresh
mid client-side route works).

!!! warning "Include last, mount at the root"
    The route is a catch-all (`/{resource:path}`). FastAPI matches in
    registration order, so include the frontend router **after** your API
    routers — that way `/api/...` beats the fallback. The wasm artifact
    references `/sw.js` at the site root, so mount it at the app root.

!!! tip "Transparent, no magic"
    - `index.html` and `sw.js` are always sent `Cache-Control: no-cache`
      (a redeploy is seen immediately); other assets use
      `asset_cache_control` (default `public, max-age=3600`).
    - Correct MIME for files `mimetypes` may not know (`.wasm` →
      `application/wasm`, `.mjs`/`.js` → `text/javascript`,
      `.webmanifest`).
    - `sw.js` gets `Service-Worker-Allowed: /` to claim the whole origin
      scope.
    - **No CSP is imposed** — it is first-party code and Pyodide needs
      `wasm-unsafe-eval`; pass `security_headers=` to add your own.
    - Path traversal (`../`) is blocked.

### Server-mode app (`build_web_app`)

The server artifact is a **live** app (`/ws` + `/sse` routes), so it is a
sub-application you mount, not a router:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import build_web_app

app = FastAPI()
# ... your API routers ...

# mount the tempestweb app (WebSocket/SSE + shell + /static) at the root:
app.mount("/", build_web_app("dist/server"))
```

`build_web_app` loads the artifact's `app.py` (its `make_state` + `view`
contract), wires the tempestweb server engine via
`tempestweb.server.create_app`, serves the client under `/static` and the
shell at `/` — the same wiring the generated `server.py` does, in-process.
You can also run it directly with uvicorn.

## Recap

- **`Page`** — typed component; declare fields, implement `body()`,
  optionally override `shell()` for a shared layout. Do not override
  `render()`.
- **`html_response(widget, *, title, status_code, htmx, document, lang)`** —
  renders and returns an `HTMLResponse`. `document=True` requires `title`;
  `document=False` returns a fragment for HTMX swaps.
- **`make_htmx_router(prefix="/_ssr")`** — serves the bundled HTMX locally
  at `GET /_ssr/htmx.js`; combine with `htmx=True`.
- **`make_web_app_router(dir)`** — serves a **wasm** (static SPA) build
  with a history fallback; include it last. **`build_web_app(dir)`** —
  hosts a **server** (WebSocket/SSE) build as a mountable sub-app.
  **`detect_build_mode(dir)`** tells them apart.
- Everything lives in the `[ssr]` extra
  (`pip install "tempest-fastapi-sdk[ssr]"`), loaded on demand —
  `import tempest_fastapi_sdk` never requires the extra.
