# UI layer (pages and components)

An interface layer **beside** `controllers`, `services` and `schemas` —
not inside them. `src/ui/` answers one question: *what does this look
like*. It never opens a database session, calls an external API or
decides a business rule.

!!! tip "When to use this recipe"
    - Your FastAPI service has to serve **HTML**, not only JSON.
    - You want pages in **typed Python**, with no template engine and no
      frontend build.
    - You want an AI agent (or another person) to know **exactly where**
      each new file goes.

    Need a reactive SPA or a compiled build? See
    [SSR (typed pages)](../ssr.md) and
    [Fullstack web](../fullstack-web.md).

## The tree, and what lives where

```text
src/
├── api/routers/       # HTTP: take the request, delegate, return a response
├── controllers/       # orchestrate services
├── services/          # business rules
├── db/repositories/   # data access
├── schemas/           # Pydantic DTOs
└── ui/                # <- the interface layer
    ├── pages/         # one class per screen
    ├── layout/        # the chrome every page inherits
    ├── components/    # reusable pieces
    └── styles.py      # the service's typed stylesheet
```

The dependency rule is one table, and it holds for every service:

| Layer | May import | Never imports |
| --- | --- | --- |
| `api/routers` | `controllers`, `ui`, `schemas` | `db` |
| `ui` | `schemas`, other parts of `ui` | `controllers`, `services`, `db` |
| `controllers` | `services`, `schemas` | `ui` |
| `services` | `db/repositories`, `schemas` | `ui` |

!!! warning "A page receives loaded data"
    A page fetches **nothing**. The router loads through a controller and
    hands the materialised data to the page. If you wrote `await` inside
    `body()`, a responsibility slipped a layer.

## A complete minimal example

Three files: the chrome, the screen, the route.

```python
# src/ui/layout/base.py
from collections.abc import Sequence
from typing import ClassVar

from tempest_core import Text, Widget

from tempest_fastapi_sdk.ui.components import NavBar, NavItem
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import Page

from src.ui.styles import CSS_URL

NAV_ITEMS: list[NavItem] = [
    NavItem(label="Home", href="/"),
    NavItem(label="Users", href="/users"),
]


class BasePage(Page):
    """Chrome shared by every screen."""

    stylesheets: ClassVar[Sequence[str]] = (CSS_URL,)
    title_suffix: ClassVar[str] = " · Tempest"

    active_href: str = "/"

    def shell(self, body: Widget) -> Widget:
        """Wrap the page body in the shared layout."""
        return Shell(
            children=[body],
            header=NavBar(items=NAV_ITEMS, active_href=self.active_href),
            footer=Text(content="Tempest", tag="small"),
        )
```

```python
# src/ui/pages/users.py
from tempest_core import Widget

from tempest_fastapi_sdk.ui.components import Card, DataTable, EmptyState

from src.ui.layout.base import BasePage


class UsersPage(BasePage):
    """The user listing."""

    users: list[dict[str, str]]

    def body(self) -> Widget:
        """Build the screen content."""
        if not self.users:
            return EmptyState(
                title="No users yet",
                description="They show up here as soon as the first one signs up.",
            )
        return Card(title="Users", children=[DataTable(rows=self.users)])
```

```python
# src/api/routers/web.py
from fastapi import APIRouter
from fastapi.responses import Response

from tempest_fastapi_sdk.ssr import html_response

from src.ui.pages.users import UsersPage

router: APIRouter = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/users")
async def users_page() -> Response:
    """Render the user listing."""
    users: list[dict[str, str]] = [{"name": "Ana", "email": "ana@example.com"}]
    return html_response(
        UsersPage(title="Users", active_href="/users", users=users),
    )
```

Piece by piece:

- **`Page`** is a `tempest_core` `Component`, which means a Pydantic
  model: the screen's data are **typed fields**, and a missing one fails
  at construction rather than at render time.
- **`body()`** returns the content widget tree. It is the only method a
  concrete screen must implement.
- **`shell()`** wraps the body. It lives on the base page and is
  inherited through ordinary Python inheritance — change the header
  once, every screen follows.
- **`stylesheets`, `head` and `title_suffix`** are **class** attributes
  (`ClassVar`), not fields: declared once on the base page, they apply to
  every screen. `html_response` reads the `<link rel="stylesheet">`, the
  extra `<head>` markup (favicon, meta) and the `<title>` from there —
  here, `"Users · Tempest"`, the page's `title` plus the suffix.
- **`html_response`** renders to HTML and returns the FastAPI response.
  The `title=`, `stylesheets=` and `head=` arguments still work, and when
  passed they **replace** what the page declares — a new route that
  forgets them no longer ships unstyled.

!!! tip "Need more than a suffix in the title?"
    Override `document_title()` on the page: it gets the instance, so it
    can build `"(3) Queue · Tempest"` from a field.

## The bundled components

The SDK ships the pieces every panel repeats. They all produce semantic
HTML through **class names**, not inline styles — the whole look lives in
the stylesheet (see [Typed CSS](ui-css.md)).

```python
from tempest_core import Text

from tempest_fastapi_sdk.ui.components import (
    Alert,
    Card,
    DataTable,
    EmptyState,
    NavBar,
    NavItem,
    Pagination,
)

Alert(message="Account created.", variant="success")
Card(title="Summary", children=[Text(content="12 orders")])
DataTable(rows=[{"name": "Ana"}])
EmptyState(title="Nothing here")
NavBar(items=[NavItem(label="Home", href="/")], active_href="/")
Pagination(page=2, pages=5, url="/users")
```

| Component | For | Detail that saves time |
| --- | --- | --- |
| `Card` | a titled block | pick the heading level with `heading_tag=` |
| `Alert` | a message by severity | `warning`/`error` render `role="alert"` |
| `DataTable` | a list of schemas | derives columns and labels from the schema |
| `Pagination` | page navigation | `pagination_for(envelope, url=...)` reads `BasePaginationSchema` |
| `EmptyState` | an empty collection | an empty collection is `200 OK`, not a 404 |
| `NavBar` | main navigation | marks the current entry with `aria-current="page"` |
| `FlashMessages` | one-shot notices after a redirect | reads what `get_flashes(request)` returns; see [SSR actions](ssr-actions.md) |

`DataTable` pays off the most: pass the **response schemas** the service
already returns and the header comes from each field's `title`.

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.components import DataTable


class UserResponseSchema(BaseModel):
    name: str = Field(title="Name")
    active: bool


table = DataTable(
    rows=[UserResponseSchema(name="Ana", active=True)],
    row_schema=UserResponseSchema,
)
```

Passing `row_schema=` keeps the header visible **even when the list is
empty** — in which case the table renders a single row carrying
`empty_text`.

### A link, button or form inside a cell

A panel table is hardly ever text only: the name opens the detail page,
and every row has a "remove" action. For that the column becomes a
`TableColumn`, with a `render=` that takes the row and returns widgets.
Text columns and `TableColumn`s mix in one list:

```python hl_lines="15-26 33-35"
from pydantic import BaseModel
from tempest_core import Text, Widget
from tempest_core.widgets import Stack

from tempest_fastapi_sdk.ui.components import DataTable, TableColumn


class FileResponseSchema(BaseModel):
    id: int
    name: str
    size: int
    owner: str


def file_actions(row: FileResponseSchema) -> list[Widget]:
    return [
        Text(content=row.name, tag="a", attrs={"href": f"/files/{row.id}"}),
        Stack(
            tag="form",
            attrs={"method": "post", "action": f"/files/{row.id}/delete"},
            children=[
                Text(content="Remove", tag="button", attrs={"type": "submit"}),
            ],
        ),
    ]


table = DataTable(
    rows=[FileResponseSchema(id=7, name="a.txt", size=120, owner="ana")],
    row_schema=FileResponseSchema,
    columns=[
        TableColumn("name", header="File", render=file_actions),
        TableColumn("size", header="Bytes", align="right"),
        TableColumn("owner", class_name="col-optional"),
    ],
)
```

The row comes out like this (measured with `tempestweb`'s HTML renderer):

```html
<tr><td><a href="/files/7">a.txt</a><form method="post" action="/files/7/delete"><button type="submit">Remove</button></form></td><td class="tui-table__cell--right">120</td><td class="col-optional">ana</td></tr>
```

!!! note "Wide table on a phone"
    The `<table>` comes out inside a `<div class="tui-table-scroll" tabindex="0">`,
    with `overflow-x: auto` and `min-width: 0`. On a 390px screen, an 849px
    table scrolls inside its own container and the page stays 390px wide
    (measured in Chromium). The wrapper takes keyboard focus for readers who
    scroll without a mouse and, with `caption=`, becomes a `region` labelled by
    the caption. The class is `ComponentClasses.table_scroll`.

Piece by piece:

- **`render=`** takes the row (the schema or the dict from `rows`) and
  returns a widget, a list of widgets or a `str`. A widget goes through
  the normal renderer — nothing is inserted as raw HTML, and the text
  inside is still escaped. A `str` becomes an escaped text cell, like the
  default; `None` becomes `none_text`.
- **`align=`** (`"left"`, `"center"`, `"right"`) applies the
  `tui-table__cell--<align>` modifier to the `<th>` and every `<td>` of
  the column. `"right"` also turns on `font-variant-numeric:
  tabular-nums`, so numbers line up on the units.
- **`class_name=`** goes on the `<th>` and every `<td>`, so your sheet
  can target the column — for example, hide it on a narrow screen:

```python
from tempest_fastapi_sdk.ui.css import Media, Rule, StyleSheet

own: StyleSheet = StyleSheet(
    reset=False,
    rules=[Media.max_width(600, [Rule(".col-optional", declarations={"display": "none"})])],
)
```

- **`header=`** changes the label of that column alone. Without it the
  usual chain applies: `headers=`, then the field's `title`, then the
  humanized name.

And pagination pairs with the SDK envelope:

```python
from tempest_fastapi_sdk.schemas import BasePaginationSchema
from tempest_fastapi_sdk.ui.components import pagination_for

envelope: BasePaginationSchema[str] = BasePaginationSchema[str](
    items=["a"], total=30, page=2, page_size=10, pages=3
)
control = pagination_for(envelope, url="/users", extra_query={"q": "ana"})
```

`extra_query` carries the active filters onto every link — the classic
pagination bug (change page, lose the search) simply cannot happen.

## Layout

`Column`, `Row` and `Spacer` from `tempest_core` already cover flexbox,
and the SDK does not duplicate them. What it adds is what was missing:

```python
from tempest_core import Text

from tempest_fastapi_sdk.ui.layout import Grid, Shell

Shell(children=[Text(content="content")], header=Text(content="top"))
Grid(children=[Text(content="a"), Text(content="b")], columns=2)
```

- **`Shell`** builds the `<header>` / `<main>` / `<footer>` landmarks —
  the structure a screen reader navigates by. By default
  (`width="contained"`) `<main>` sits in a centred `72rem` column with a
  gutter on each side — right for a reading page. `Shell(width="full")`
  adds the `tui-shell__main--full` class, which clears the maximum width,
  the margin and the padding: the classic panel layout, with a sidebar
  flush against the edge, spans the whole screen and handles its own
  spacing, with no CSS override from you.
- **`Grid`** is a real CSS grid. Without `columns=` it auto-fits
  (`minmax(16rem, 1fr)`), so it collapses to one column on a phone with
  no media query at all.

## Components of your own

Any subtree becomes a typed `Component`. It is the same mechanism `Card`
and `Alert` use.

```python
from tempest_core import Text, Widget
from tempest_core.widgets import Component, Stack


class Stat(Component):
    """One big number with its label underneath."""

    label: str
    value: str

    def render(self) -> Widget:
        """Compose the metric."""
        return Stack(
            tag="div",
            attrs={"class": "stat"},
            children=[
                Text(content=self.value, tag="strong"),
                Text(content=self.label, tag="small"),
            ],
        )
```

!!! info "`Stack` for semantic HTML, `Column`/`Row` for flexbox"
    The renderer injects `display: flex` into `Column`/`Row` **by widget
    type**, even with no style. A `<select>` or `<table>` with
    `display: flex` breaks. `Stack` renders a bare element with no
    injected style — the right container for semantic markup. Measured,
    and pinned in `tests/ui/test_core_contract.py`.

In a `Component` you override `render()`. `body()` and `shell()` exist
only on `Page`.

## The scaffold writes the whole layer

```bash
tempest new my_service --extras "ssr"
```

That writes a complete `src/ui/` — `styles.py`, `layout/base.py`,
`components/stat.py`, `pages/home.py` — plus `api/routers/web.py` wiring
the three together. In a project that already exists:

```bash
tempest generate --src
```

It reads the extras pinned in your `pyproject.toml` and writes only the
missing layers, never touching an existing file (unless you pass
`--force`).

All that is left is including both routers in `create_app`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ui.css import make_css_router

from src.api.routers.web import router as web_router
from src.ui import CSS_PATH, STYLESHEET

app: FastAPI = FastAPI()
app.include_router(make_css_router(STYLESHEET, path=CSS_PATH))
app.include_router(web_router)
```

The router serves at `CSS_PATH`, and the generated pages link `CSS_URL`
(`STYLESHEET.url(CSS_PATH)`), the URL carrying the content version —
[Typed CSS](ui-css.md#serving-the-sheet) explains the caching that buys.

## Recap

- `ui` is a layer, beside `controllers` and `services`, and answers only
  "what does this look like".
- `ui/pages/` holds one class per screen; `ui/layout/` the chrome they
  all inherit; `ui/components/` the pieces; `ui/styles.py` the sheet.
- The page receives loaded data from the router — no I/O inside
  `body()`.
- `Stack` for semantic markup, `Column`/`Row` for flexbox.
- `DataTable` takes `TableColumn(render=..., align=..., class_name=...)`
  for a link, button or form per row, with no hand-written table.
- `Shell(width="full")` frees the whole width for a panel layout.
- `tempest new --extras "ssr"` writes all of it, working.

Next: [Forms from Pydantic schemas »](ui-forms.md) and
[Typed CSS »](ui-css.md).
