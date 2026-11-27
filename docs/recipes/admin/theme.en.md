---
description: "Use to change the admin colors, logo, favicon, font, radius, footer and dark mode through typed `AdminTheme` parameters."
---

# Customize the look — `AdminTheme`

Use to change how the panel looks (colors, logo, favicon, font, dark mode) without forking the stylesheet, by passing an `AdminTheme`.

Part of the [Admin site](../admin.md) recipe.

The admin CSS is driven entirely by **CSS custom properties** on `:root`. Instead of forking the stylesheet, you pass an `AdminTheme` with **typed, documented parameters** — colors, logo, favicon, font, radius, footer, dark mode — and the SDK injects a `<style>` block in the `<head>` (after `admin.css`, so it wins).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # primary color (links, buttons, active item)
    accent_hover="#6d28d9",                 # hover shade of the accent
    header_bg="#1e1b4b",                    # header/sidebar background (text turns light on its own)
    radius="10px",                          # radius of buttons, inputs, cards, tables
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # header image (instead of the text brand)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # dark surfaces
)

site: AdminSite = AdminSite(title="Servus Admin", brand="Servus", theme=theme)
```

`AdminTheme()` with no arguments is a **no-op**: it reproduces the stock look. You only set what you want to change.

!!! tip "The golden rule"
    Every `AdminTheme` field maps to a `:root` CSS variable (or to a piece
    of chrome, like the logo). It is all typed — the editor autocompletes
    the options and mypy validates — and no string ever needs to be a CSS
    class name or selector.

| Field | Type | Default | Effect |
|-------|------|---------|--------|
| `accent` | `str` | `"#2563eb"` | Primary color: links, buttons, active sidebar item |
| `accent_hover` | `str` | `"#1d4ed8"` | Hover/active shade of `accent` |
| `danger` | `str` | `"#b91c1c"` | Destructive actions and error messages |
| `header_bg` | `str` | `"#ffffff"` | Header background |
| `sidebar_bg` | ``str | None`` | `None` | Sidebar background (falls back to `header_bg`) |
| `header_fg` | ``str | None`` | `None` | Header and sidebar text color (`None` picks it from the background contrast) |
| `page_bg` | ``str | None`` | `None` | Content background (mode default) |
| `radius` | `str` | `"8px"` | Radius of buttons, inputs, cards, tables |
| `font_family` | ``str | None`` | `None` | `font-family` for the whole panel |
| `logo_url` | ``str | None`` | `None` | Header image instead of the text brand |
| `logo_alt` | `str` | `"Logo"` | `alt` text for the logo image |
| `favicon_url` | ``str | None`` | `None` | Browser-tab favicon |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Footer text |
| `dark_mode` | `bool` | `False` | Dark surfaces (and the chrome, while `header_bg` is the default) |
| `custom_css_url` | ``str | None`` | `None` | Extra stylesheet, linked last |

!!! info "Dark mode"
    `dark_mode=True` switches the **content surfaces** (page background,
    cards, text, table rows, inputs, borders) to a dark palette. The header
    and sidebar go dark with them while `header_bg` is the light default;
    a `header_bg` of yours is kept. `accent` and the other colors still
    apply. An explicit `page_bg` wins over the content's dark mode (the
    surfaces stay light), but the default chrome still goes dark.

!!! info "Header text picked from the background"
    The header and sidebar are light by default. Give them a dark hex
    `header_bg` (or `sidebar_bg`) and their text turns light on its own —
    the SDK computes the background's luminance. A color that is not hex
    (`"rebeccapurple"`, `rgb(...)`) is treated as dark; when that guess is
    wrong, pin the color with `header_fg`.

!!! warning "Escape hatch for the rest"
    For anything the fields do not cover, point `custom_css_url` at your own
    stylesheet. It is linked **after** the theme, so it overrides
    everything — including `AdminTheme`.

!!! danger "Values are developer-set, not end-user input"
    The characters `< > { } "` are rejected in any string field
    (`ValueError` at construction), because they would break the injected
    `<style>` block or an HTML attribute. Never derive `AdminTheme` values
    from end-user input.

**Recap:** instantiate `AdminTheme` with the fields you want to change,
pass it via `AdminSite(theme=...)`, and the look changes across every page
(login, dashboard, list, detail, forms) without touching CSS. For full
control, `custom_css_url`.
