# Sharing with a preview (Open Graph)

When someone pastes a link into WhatsApp, Telegram, Instagram or X, the
network fetches that URL and builds the preview (image, title and
description) from the **Open Graph** tags in the HTML it gets back. Those
crawlers **do not run JavaScript**: an SPA returns an empty `index.html`, and
the preview comes out blank.

So the **backend** answers the shared link. It returns, per item, a page with
the tags already in place and the preview image. `tempest_fastapi_sdk.share`
builds that from one function of yours that says what to show for each item.

By the end of this recipe you will have:

- `GET /s/{id}`: the HTML with `og:title`, `og:description`, `og:image`,
  `og:url`, `twitter:card` and `<link rel=canonical>`, with the human sent on
  to the app screen;
- `GET /s/{id}/og.png`: a 1200×630 card drawn with your brand, with `ETag` and
  `304`;
- a generic preview for an item that no longer exists, with no `500` and no
  leaked data.

## Installation

The page needs nothing beyond the base install. The PNG card uses Pillow,
which comes with the `[image]` extra:

```bash
uv add "tempest-fastapi-sdk[image]>=0.371.0"
```

The card also needs a TrueType font installed on the machine. On
Debian/Ubuntu that is the `fonts-dejavu-core` package. See
[Font in Docker](#font-in-docker).

## Step 1: the item page

You write **one** function: it gets the id from the URL and returns a
`SharePreviewSchema`, or `None` when the item must not be shown.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.share import SharePreviewSchema, make_share_router

TRIPS: dict[str, dict[str, str]] = {
    "42": {"origin": "Teresina", "destination": "Fortaleza", "when": "Friday, 6pm"},
}


async def resolve_trip(item_id: str, /) -> SharePreviewSchema | None:
    trip = TRIPS.get(item_id)
    if trip is None:
        return None
    return SharePreviewSchema(
        title=f"Ride {trip['origin']} → {trip['destination']}",
        description=f"{trip['when']} · book your seat in the app",
        redirect_url=f"https://app.example.com/rides/{item_id}",
    )


app = FastAPI()
app.include_router(
    make_share_router(
        resolve_trip,
        public_url="https://api.example.com",
        fallback=SharePreviewSchema(
            title="BusCar",
            description="Rides between cities",
            redirect_url="https://app.example.com",
        ),
        site_name="BusCar",
        locale="en_US",
        link_text="Open in the app",
    )
)
```

Piece by piece:

- **`resolve_trip`** is the resolver. The `/` after `item_id` is not
  decoration: the router calls the resolver positionally, so the parameter
  name is yours to pick.
- **`redirect_url`** is where the **person** who clicks the link goes. The
  page sends the browser there with `<meta http-equiv="refresh">` and also
  shows a visible link (`link_text`) for when refresh is blocked.
- **`public_url`** is the public origin the link is served from. `og:url` and
  the image URL are built on it.
- **`fallback`** is the preview shown when the resolver returns `None`.
- **`site_name`** and **`locale`** are the same for every item, so they live
  on the router, not on the schema.

With the app running, `GET /s/42` returns:

```html
<title>Ride Teresina → Fortaleza</title>
<link rel="canonical" href="https://api.example.com/s/42">
<meta property="og:type" content="website">
<meta property="og:title" content="Ride Teresina → Fortaleza">
<meta property="og:description" content="Friday, 6pm · book your seat in the app">
<meta property="og:url" content="https://api.example.com/s/42">
<meta property="og:site_name" content="BusCar">
<meta property="og:locale" content="en_US">
<meta name="twitter:card" content="summary">
```

Both routes answer `GET` and `HEAD`, and stay **out of OpenAPI** by default. They are public pages, not
API. Pass `include_in_schema=True` to see them in `/docs`.

!!! warning "Why `public_url` is required"
    The router never builds a URL from `request.base_url`. Behind nginx that
    value is the internal address (`http://backend:8000`), and the `Host`
    header is the client's to choose. `og:image` must be absolute and public:
    the crawler has no page to resolve a relative path against, and an
    image it cannot find raises no error you would ever see.

### What is escaped, and what is refused

Every value that enters the HTML goes through `html.escape(..., quote=True)`:
title, description, alt text, `site_name`, `locale`, `link_text` and the
URLs. An item named `"><script>` becomes text, not code.

Escaping is not enough for a URL: `javascript:alert(1)` still runs inside
`href` after escaping. So `redirect_url`, `canonical_url` and `image_url`
only accept an **absolute** `http`/`https` URL. The schema refuses the rest
(`javascript:`, `data:`, `/path`, `//host`) with `ValidationError`, before
any HTML exists.

## Step 2: the unavailable item

When the resolver returns `None`, the page **still answers `200`**, with the
`fallback` text. `og:url` is still the shared link. A link someone sent before
the ride was cancelled opens the generic preview: it neither breaks nor tells
what the ride was.

Return `None` for anything that must not be shown: deleted, private, sold out
or another tenant's. An exception in the resolver does **not** become the
fallback: it propagates and becomes a `500`. A database outage is a failure,
not an "unavailable item", and swallowing it hides the problem.

## Step 3: the PNG card

Pass a `ShareCardRenderer` and fill `card` on the preview:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.share import (
    ShareCardLineSchema,
    ShareCardRenderer,
    ShareCardSchema,
    SharePreviewSchema,
    make_share_router,
)

TRIPS: dict[str, dict[str, str]] = {
    "42": {
        "origin": "Teresina",
        "destination": "Fortaleza",
        "when": "Friday, 11/21 at 6pm",
        "stops": "Piripiri · Sobral",
        "price": "R$ 80 per seat",
        "seats": "3 seats",
        "driver": "Ana Souza (4.9)",
        "updated_at": "2026-11-20T14:03:00+00:00",
    },
}


async def resolve_trip(item_id: str, /) -> SharePreviewSchema | None:
    trip = TRIPS.get(item_id)
    if trip is None:
        return None
    route = f"{trip['origin']} → {trip['destination']}"
    return SharePreviewSchema(
        title=f"Ride {route}",
        description=f"{trip['when']} · {trip['price']}",
        redirect_url=f"https://app.example.com/rides/{item_id}",
        version=trip["updated_at"],
        card=ShareCardSchema(
            brand="BusCar",
            kicker="Ride available",
            title=route,
            lines=[
                ShareCardLineSchema(icon="→", text=f"Stops: {trip['stops']}"),
                ShareCardLineSchema(icon="★", text=trip["when"]),
            ],
            chips=[trip["price"], trip["seats"]],
            footer=f"Driver: {trip['driver']}",
        ),
    )


renderer = ShareCardRenderer(
    background=("#0b3d91", "#061f4a"),
    accent_color="#ffd166",
)

app = FastAPI()
app.include_router(
    make_share_router(
        resolve_trip,
        public_url="https://api.example.com",
        fallback=SharePreviewSchema(
            title="BusCar",
            description="Rides between cities",
            redirect_url="https://app.example.com",
        ),
        renderer=renderer,
        site_name="BusCar",
        locale="en_US",
    )
)
```

Now the page gets `og:image` (with `og:image:width` 1200 and
`og:image:height` 630) and `twitter:card` becomes `summary_large_image`.
`GET /s/42/og.png` returns this (the Portuguese version of the same card):

![1200x630 share card with brand, route, stops, time, price and seat chips and the driver's name](../assets/share-card.png)

The layout, top to bottom:

| Field | Where | When it does not fit |
| --- | --- | --- |
| `brand` | top, accent color | the font shrinks, then the text is cut with `…` |
| `kicker` | under the brand, muted color | the font shrinks, then the text is cut |
| `title` | bold, from 72 px down to 48 px on one line | the font shrinks to 48 px; if it still does not fit, wraps to two lines at 56 px, then is cut |
| `lines` | accent-colored icon + text | a line that does not fit above the chips is dropped |
| `chips` | rounded labels | a chip that would cross the margin is dropped |
| `footer` | bottom, muted color | the font shrinks, then the text is cut |

Every card text stays on one line: line breaks and repeated spaces become one
space (`"Friday\n6pm"` becomes `"Friday 6pm"`), and an empty chip is dropped.
A description typed with Enter does not bring the image down.

No text leaves the margin. The test
`tests/share/test_card.py::TestRender::test_long_text_stays_inside_margins`
pins that with 80 words in every field.

The renderer takes any color Pillow understands (`"#0b3d91"`,
`"rgb(11, 61, 145)"`, `"navy"`) and a `logo` (`bytes` or `Path`, PNG with
transparency), drawn at the top right, 72 px tall and at most 352 px wide (a
wider logo is scaled down until it fits). An invalid color, a logo
that is not an image and a missing font fail **at construction**, when the
service boots, not at the first share.

!!! tip "The icon is a character of the font"
    `icon` is drawn with the card's font. `→`, `★`, `✓` and `·` exist in
    DejaVu. A color emoji does not, and comes out as a box.

### Off the event loop

Drawing the card is CPU work: about 49 ms per card, the mean of 20
consecutive renders of a card with every field, on Pillow 13.0.0, on the
SDK's development machine (WSL2). The router calls `renderer.render` through
`asyncio.to_thread`, so the loop keeps serving other requests meanwhile. The
test `tests/share/test_router.py::TestEventLoop` checks that the render runs
on another thread, and fails if `to_thread` becomes a direct call.

## Step 4: cache, version and WhatsApp

Two cache layers are in play, and each asks for something different.

**The `ETag` avoids redrawing.** `og.png` answers with a weak `ETag` and
`Cache-Control: public, max-age=600` (change it with `cache_control=`). When
the client revalidates with `If-None-Match` and the tag matches, the answer
is `304` **without drawing anything**: one resolver call, no rendering. The
comparison follows RFC 9110 and accepts `W/"..."`, a list of tags and `*`.

The tag is derived from:

- the preview's `version` (your `updated_at`) or, without `version`, the
  `card` content;
- the renderer fingerprint (colors, fonts and logo): changing the brand color
  invalidates every card;
- the item id.

The tag is **weak** because two replicas with different FreeType builds can
produce different bytes for the same card. It promises "same content", not
"same bytes".

**The `?v=` makes the network fetch the new image.** The crawler keeps what
it fetched, keyed by URL. How long it keeps it, and whether it revalidates,
is each network's decision, outside your control. If the URL were always
`/s/42/og.png`, an edited ride could keep showing the old preview, `304` or
not. So the page publishes the image as `/s/42/og.png?v=<version hash>`. A
new `version` is a new URL, and to the network a different image.

!!! warning "Fill `version`"
    Without `version` the hash comes from the card content. That works, but
    the resolver builds the whole card on every revalidation. With
    `updated_at` in `version` the resolver can answer quickly, and the hash
    changes exactly when the item changes.

## Step 5: the public link, live

The link the driver shares is `https://<domain>/s/42`. Two ways to get that
path to the backend:

**nginx in front of the app and the API.** Forward the prefix to the backend
and leave the rest to the SPA:

```nginx
location /s/ {
    proxy_pass http://backend:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location / {
    root /usr/share/nginx/html;
    try_files $uri /index.html;
}
```

Here `public_url` is the app's domain (`https://app.example.com`), because
that is what appears in the link.

**The backend serves the SPA.** With
[`make_spa_router`](react-spa.md), include the share router **before** the
SPA. FastAPI matches routes in registration order, so `/s/{id}` matches
first. In the reverse order the SPA fallback answers `/s/42` with
`index.html`, and the preview comes out blank.

To check what the crawler sees, make the same request it makes:

```bash
curl -s https://app.example.com/s/42 | grep 'property="og:'
curl -sI https://app.example.com/s/42/og.png
```

To force a network to re-read a link already shared, use its tool. For
Facebook that is the
[Sharing Debugger](https://developers.facebook.com/tools/debug/), with
"Scrape Again".

## Font in Docker

A `python:3.12-slim` image ships no TrueType font. Without one,
`ShareCardRenderer()` **does not start**:

```text
FileNotFoundError: no TrueType font found for the share card. Install one
(`apt-get install fonts-dejavu-core` on Debian/Ubuntu) or pass font_path= to
the renderer.
```

The SDK does not fall back to Pillow's bundled font on purpose. Measured on
Pillow 13.0.0, that font draws `ã` and `→` with the same box as a missing
character (`U+F0000`). A Portuguese preview would come out full of squares,
without any error.

Two ways out:

- install the font in the image. `tempest generate --dockerfile` already
  emits this line when the project pins the `[image]` extra:

    ```dockerfile
    RUN apt-get update \
        && apt-get install -y --no-install-recommends fonts-dejavu-core \
        && rm -rf /var/lib/apt/lists/*
    ```

- or pass the file: `ShareCardRenderer(font_path=Path("fonts/Inter.ttf"),
  bold_font_path=Path("fonts/Inter-Bold.ttf"))`. An explicit path that does
  not exist is an error. The renderer does not look for another font in its
  place, because a typo there would make production draw with a font other
  than the one that was reviewed.

Without `font_path` the renderer looks, in order, for DejaVu on
Debian/Ubuntu, Fedora and Arch, then Liberation. The list is
`DEFAULT_FONT_CANDIDATES`.

## Just the page, no Pillow

Already have the image on a CDN? Do not pass `renderer` and fill `image_url`:

```python
from tempest_fastapi_sdk.share import SharePreviewSchema


async def resolve_product(item_id: str, /) -> SharePreviewSchema | None:
    return SharePreviewSchema(
        title="Running shoes",
        description="US$ 59.90 · free shipping",
        redirect_url=f"https://shop.example.com/p/{item_id}",
        image_url=f"https://cdn.example.com/products/{item_id}.jpg",
    )
```

Without `renderer` the `og.png` route is not even registered, and the base
install is enough. `image_url` also works with a renderer: when set, it wins
over the generated card.

## Recap

- **`make_share_router(resolver, public_url=..., fallback=...)`** serves
  `GET /s/{id}` with the Open Graph and Twitter tags, all escaped, and sends
  the human on to `redirect_url`.
- **`SharePreviewSchema`** refuses any URL that is not absolute
  `http`/`https`. Escaping does not neutralize `javascript:`; refusing does.
- **The resolver returns `None`** for an unavailable item. The answer is
  `200` with the `fallback`, leaking nothing. An exception propagates as a
  `500`.
- **`ShareCardRenderer`** draws the 1200×630 card with your brand. Long text
  shrinks, wraps or is cut, and never leaves the margin. Without a font it
  fails at boot instead of drawing squares.
- **`version`** feeds the `ETag` (a `304` with no drawing) and the image's
  `?v=` (a new URL the network fetches again).
- **`public_url`** comes from configuration, never from the request's
  `Host`.
