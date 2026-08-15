# Typed CSS (stylesheet and tokens)

CSS written in Python, checked by the type checker, served by the app
itself. No loose `.css` file, no frontend build, no CDN.

!!! tip "When to use"
    - You need a **selector, a pseudo-class or a media query** — things
      an inline style cannot express.
    - You want a consistent palette, spacing and typography, dark mode
      included, without hand-maintaining a colour table.
    - You want a wrong class name to **fail**, instead of rendering an
      unstyled element.

    `tempest_core`'s `Style` is still the right tool for a widget's local
    layout. This recipe is about the sheet.

## A rule is an object

```python
from tempest_core import Style
from tempest_core.style import Edge

from tempest_fastapi_sdk.ui.css import Media, Rule, StyleSheet

sheet: StyleSheet = StyleSheet(
    rules=[
        Rule(".card", style=Style(padding=Edge.all(16), radius=8.0)),
        Rule(".card:hover", declarations={"cursor": "pointer"}),
        Media.min_width(768, [Rule(".card", declarations={"padding": "24px"})]),
    ],
)
print(sheet.to_css())
```

Out comes exactly what you expect:

```css
.card {
  padding: 16px 16px 16px 16px;
  border-radius: 8px;
}

.card:hover {
  cursor: pointer;
}

@media (min-width: 768px) {
  .card {
    padding: 24px;
  }
}
```

A `Rule` takes declarations from two places, and the split is
deliberate:

- **`style=`** — a typed `Style`, converted by the **same** function the
  renderer uses for widgets. A rule and an inline style with the same
  values emit identical declarations.
- **`declarations=`** — a plain mapping, for what `Style` does not model:
  `display: grid`, `cursor`, `content`, and **every** token reference.

There is also `layout="column"` / `"row"`, which applies `display: flex`
plus `flex-direction` the way `Column` and `Row` do — so `gap`,
`justify` and `align` are not silently inert.

!!! warning "Colours in `Style` are hex only"
    `Style(color="var(--t-color-primary)")` raises `invalid hex color` —
    measured against `tempest_core`'s validator. Token references go in
    `declarations`: `Rule(".btn", declarations={"color":
    theme.color("primary")})`.

## Design tokens, from `tempest_core` into CSS

The palette is not reinvented here: `ThemeTokens` adapts
`tempest_core`'s `TokenSet` (the same one the client uses) into custom
properties.

```python
from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, ThemeTokens

theme: ThemeTokens = ThemeTokens()
sheet: StyleSheet = StyleSheet(
    theme=theme,
    rules=[
        Rule(
            ".card",
            declarations={
                "padding": theme.space("md"),
                "border-radius": theme.radius("md"),
                "background": theme.color("surface"),
                "color": theme.color("on_surface"),
                "font-size": theme.font_size("body_medium"),
            },
        ),
    ],
)
```

That emits three blocks: `:root` with the light scheme and every scale,
`@media (prefers-color-scheme: dark)` (guarded so an explicit light
choice wins) and `:root[data-theme="dark"]` for a toggle. Writing
`theme.color("surface")` once settles both modes.

Available groups: `color` (39 roles — `primary`, `on_surface`,
`error_container`, …), `space`, `radius`, `font-size`, `line-height`,
`font-weight`, `letter-spacing`, `duration`, `easing`.

Breakpoints are the exception, for a reason: a media query cannot read
`var()`. So they come back as numbers:

```python
from tempest_fastapi_sdk.ui.css import Media, Rule, ThemeTokens

theme: ThemeTokens = ThemeTokens()
wide = Media.min_width(
    theme.breakpoint("lg"),
    [Rule(".sidebar", declarations={"display": "block"})],
)
```

Besides `min_width` there are `max_width`, `dark()` and
`reduced_motion()`.

## Serving the sheet

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import make_css_router

app: FastAPI = FastAPI()
app.include_router(make_css_router(app_stylesheet(), path="/static/app.css"))
```

The CSS is rendered **once**, when the router is built — no request pays
for walking the rules. The response carries a content-derived `ETag`, and
a matching `If-None-Match` gets a bodyless `304`.

On the page side, point the `<link>`:

```python
from tempest_core import Text

from tempest_fastapi_sdk.ssr import html_response

response = html_response(
    Text(content="Hello", tag="h1"),
    title="Home",
    stylesheets=["/static/app.css"],
)
```

## The ready sheet, and yours on top

`app_stylesheet()` composes what nearly every service wants: tokens, a
minimal reset, form rules and component rules.

```python
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, ThemeTokens

theme: ThemeTokens = ThemeTokens()
own: StyleSheet = StyleSheet(
    reset=False,
    rules=[
        Rule(
            ".page-title",
            declarations={
                "margin": "0",
                "font-size": theme.font_size("headline_small"),
                "color": theme.color("on_background"),
            },
        ),
    ],
)
sheet: StyleSheet = app_stylesheet(theme=theme, extra=own)
```

Your rules come last, so they win on equal specificity — the ordinary
cascade. `merge()` does the same between any two sheets, and
`StyleSheet(reset=False)` turns the reset off.

## A wrong class name should hurt

A typo in `class="crad"` breaks nothing: the element simply renders
unstyled and someone finds out in production. `StyleSheet.cls()` turns
that into an error at render time:

```python
from tempest_core import Column, Text

from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, cls

sheet: StyleSheet = StyleSheet(rules=[Rule(".card", declarations={"padding": "16px"})])

Column(tag="section", attrs=sheet.cls("card"), children=[Text(content="hi")])
Column(tag="section", attrs=cls("card", "card--wide"), children=[])
```

`sheet.cls("crad")` raises `KeyError` listing the classes that do exist.
The free `cls()` does not validate — reach for it when the class comes
from another sheet (an external design system, say).

You can push that into a test instead of relying on discipline:

```python
import re

from tempest_fastapi_sdk.ui import app_stylesheet


def test_page_uses_only_defined_classes(html: str) -> None:
    """Fail when the page uses a class the sheet does not define."""
    used = {
        name
        for attribute in re.findall(r'class="([^"]+)"', html)
        for name in attribute.split()
    }
    assert used <= app_stylesheet().class_names()
```

That test exists in the SDK's own suite, and found two unstyled classes
the first time it ran.

## Recap

- `Rule` + `Media` + `StyleSheet` cover selectors, pseudo-classes and
  media queries — what an inline style cannot reach.
- `style=` for typed values, `declarations=` for the rest and for every
  token (`Style` only accepts hex colours).
- `ThemeTokens` translates `tempest_core`'s token set into custom
  properties, light and dark at once.
- `make_css_router` serves the sheet rendered once, with `ETag` and
  `304`.
- `app_stylesheet()` already brings tokens, reset, forms and components;
  your rules go in through `extra=`.
- `sheet.cls(...)` turns a class typo into a `KeyError`.

See also: [UI layer »](ui.md) and [Forms from Pydantic schemas »](ui-forms.md).
