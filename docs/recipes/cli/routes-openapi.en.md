---
description: "Use to list routes, bundle a route for an AI (`tempest bundle-route`), document errors in OpenAPI and audit permission guards."
---

# Routes, OpenAPI errors and permissions

Use to list routes, bundle a route for an AI, document errors in OpenAPI and audit permission guards.

Part of the [CLI](../cli.md) recipe.

## Routes — `tempest routes`

Lists the URLs the application **actually** serves.

```bash
tempest routes
```

```text
METHOD  PATH                    NAME          SCHEMA  GUARDS
GET     /api/items/{item_id}    read_item     yes     require_admin
POST    /api/items/             create_item   yes     require_admin
GET     /health                 health        yes     -
GET     /painel                 painel        no      -
```

Four things in that table reading the code does not hand you:

- **A mounted router shows up.** Since FastAPI 0.141.1, `include_router`
  keeps an `_IncludedRouter` entry instead of flattening the routes onto the
  application — so `{r.path for r in app.routes}` contains **none** of the
  included router's paths. The command reads the same expansion the OpenAPI
  generator walks.
- **Prefixes already resolved.** The path is the router's plus every
  `include_router` above it.
- **Routes outside the schema are listed too**, with `SCHEMA=no` — an HTML
  page and an internal endpoint serve traffic, and they are exactly what an
  answer taken from `openapi.json` hides.
- **`GUARDS` shows the dependency inherited from the include**, not only the
  one declared on the route. That is how you confirm the
  `Depends(require_admin)` on `include_router` reached everything it should.

Filters and format:

```bash
tempest routes --match /api/orders        # only paths containing this
tempest routes --method post,delete       # only these methods
tempest routes --all                      # include /docs, /redoc, /openapi.json
tempest routes --json                     # JSON array, for scripting
tempest routes --app src.server:app       # app outside the conventional spots
```

Without `--app`, the command probes `src.server:app`,
`src.api.app:create_app`, `main:app` (and the same under `app/`). When
nothing resolves it **lists what it tried and why** before exiting 2. A
`--match` that matches nothing exits 1, so a typo in a script fails instead
of reading as an application with no routes.

## Bundle a route for an AI chat — `tempest bundle-route`

An endpoint has a bug and you want to ask GPT, Gemini or Claude about it. The
answer is only good if the model sees the whole request path: the router, the
dependency that builds the controller, the controller, the service, the
repository, the model and the schemas. Pasting that by hand means opening
eight files — and the one you forget is usually the one with the bug.

`tempest bundle-route` builds that bundle for you, as markdown:

```bash
tempest new demo
cd demo
cp .env.example .env
tempest bundle-route "GET /api/"
```

The start of the output, measured on a freshly generated project:

```text
# Route bundle: `GET /api/`

- **Method:** `GET`
- **Path:** `/api/`
- **Endpoint:** `index`
- **Dependencies:** `get_service_info_controller`, `get_settings`

## Files (6 files, 277 lines)

| Layer | File | Lines |
| --- | --- | --- |
| router | `src/api/routers/service_info.py` | 30 |
| dependencies | `src/api/dependencies/controllers.py` | 37 |
| controllers | `src/controllers/system/service_info.py` | 40 |
| schemas | `src/schemas/service_info.py` | 23 |
| core | `src/core/settings.py` | 117 |
| other | `src/scripts/composition.py` | 30 |
```

After the table comes every file **whole**, in a `` ## `path` `` section with
a `python` block, in the same order. Paste it all into the chat and write your
question underneath.

On a real domain the bundle grows the way you would expect: with the
`products` domain the generated `CLAUDE.md` teaches you to write,
`tempest bundle-route "GET /api/products"` gave **15 files, 635 lines** —
router, `api/dependencies/controllers.py` and `resources.py`, controller,
service, repository, model, schema, `core/constants/messages/errors.py`,
`core/exceptions/conflict.py` and `not_found.py`, `core/settings.py`,
`db/configs/connection.py` and `names.py` and `scripts/composition.py`.
No `service_info` file.

The summary goes to `stderr`, so `stdout` carries only the markdown:

```text
GET /api/: 6 file(s), 277 line(s). The bundle is your source code: review it before pasting it anywhere.
```

### How it finds the files

1. **The route is resolved on the live app**, through the same expansion
   `tempest routes` uses — the `include_router` prefix already applied. The
   starting points are the endpoint and its **whole** `Depends` tree,
   including the dependency inherited from `include_router`.
2. **From there on, everything is static.** Each project module is read with
   `ast`; nothing beyond the app itself is imported or executed.
3. **The walk is per symbol, not per module.** The scaffold tells you to
   import from the package (`from src.services import ProductService`), and
   every `__init__.py` re-exports every domain. Following the whole module
   would drag the whole project in. Instead, the name is followed through the
   re-export to the file that **defines** it, and only the names that
   definition uses are followed next.
4. **A file goes in whole** once any definition in it is reached. That is why
   `api/dependencies/controllers.py` shows up with the other domains'
   providers inside — but not those domains' controllers. An `__init__.py`
   crossed only as a re-export does not go in.

Files are ordered by layer: router, dependencies, controllers, services,
repositories, models, schemas, core, then the rest. The endpoint's file
always comes first.

!!! warning "The bundle is your source code"
    The command only reads `.py` files of the project's packages (`src/` or
    `app/`) under the root — **never** the `.env`, the standard library,
    `site-packages`, a `.venv` inside the project, or the SDK itself. But what
    it reads is your code, whole, and it may be proprietary. A value written
    in the code (a `Settings` default, a test key) goes along. Review it
    before pasting it into a third-party service: where the code goes is your
    call.

### Picking the route

The argument is `"METHOD /path"`, with the path `tempest routes` prints. The
method is case-insensitive. Without a method, the path must have exactly one:

```bash
tempest bundle-route "GET /api/products"
tempest bundle-route /api/                               # a single method serves that path
tempest bundle-route "get /api/"                         # lowercase method works too
```

When the choice is not unique, the command stops instead of guessing:

- **A path with several methods and none named** exits with code `2` and
  lists the methods: on the recipe domain, `tempest bundle-route /api/products` answers `error: /api/products is served by several methods: GET, POST. Name one, e.g. 'GET /api/products'.`
- **A route that does not exist** exits with code `1` and suggests close
  matches:

```text
error: no route matches 'GET /health'.
  did you mean: GET /health/liveness
  did you mean: GET /health/readiness
  did you mean: GET /api/
  did you mean: GET /redoc
  did you mean: GET /openapi.json
  run `tempest routes` to list every route.
```

- **A route served by installed code** (the SDK's `/health/liveness`, for
  instance) reaches no project file and exits with code `1`.

### Options

```bash
tempest bundle-route "GET /api/" --output bug.md         # writes the file, stdout stays empty
tempest bundle-route "GET /api/" --max-depth 0           # only the files defining the endpoint and dependencies
tempest bundle-route "GET /api/" --app src.server:app    # app outside the conventional locations
```

`--max-depth` counts hops from the endpoint and its dependencies. With `0`,
the same `GET /api/` route drops to **3 files, 184 lines** (router,
`api/dependencies/controllers.py` and `core/settings.py`, where
`get_settings` lives). Without the option, everything reachable goes in.

!!! tip "The app has to import"
    Like `tempest routes`, the command imports the project's app, so the
    `.env` (or the environment variables) must be in place — hence the
    `cp .env.example .env` in the example. When the import fails it lists
    what it tried and why, and exits with code `2`.

### Recap

- `tempest bundle-route "METHOD /path"` gathers one route's code into
  markdown ready to paste into an AI chat.
- The route comes from the live app; the code is followed per symbol,
  statically, without crossing into other domains.
- Only the project's `.py`, never `.env` or installed packages — and it is
  still your code: review it before pasting.

## Errors documented in OpenAPI — `tempest openapi-errors`

Compares, per route, the `AppException`s the flow can raise against what the
route declared in `error_responses(...)` / `@raises(...)`:

```bash
tempest openapi-errors                          # advisory report (exit 0)
tempest openapi-errors --check                  # exit 1 on drift (CI gate)
tempest openapi-errors --path src --path libs   # explicit directories, repeatable
tempest openapi-errors --check --allow-unreachable   # fail only on undocumented
tempest openapi-errors --fix --dry-run          # diff of the missing declarations
tempest openapi-errors --fix                    # write (needs a clean git tree)
```

Without `--path` it scans `./src` or `./app` — whichever exists. The analysis is
static (`ast`, without importing the application) and walks `router ->
controller -> service -> repository`, reading both `raise` statements and the
docstrings' `Raises:` sections.

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
1 route(s) with drift, 2 undocumented exception(s).
```

`--fix` closes the gap by writing `responses=error_responses(...)` into the route
(plus whatever imports are missing), extending an existing declaration instead of
replacing it. It only ever adds: `unreachable` findings are never removed, since
reachability cannot see a dynamic raise.

Details and limitations in the [Errors in OpenAPI »](../openapi-errors.md) recipe.

---

## Permission guards — `tempest permissions`

Reads the `@requires` guard contract straight off the source (`ast`, without
importing the application) and reports what the decorator cannot see at import
time:

```bash
tempest permissions                    # informative report (exit 0)
tempest permissions --check            # exit 1 on any error (CI gate)
tempest permissions --check --strict   # fail on warnings too
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
2 finding(s), 1 error(s).
```

Errors: a guard raising outside the `AppException` hierarchy, a predicate-style
guard (`-> bool`), wrong arity, an `async` guard on a sync function, a route with
no user parameter. Warnings: a guard that never denies, a missing annotation, a
guard the checker could not resolve (lambda, duplicated name, definition outside
the scanned paths).

Details in the [Permission guards (`@requires`) »](../permission-guards.md) recipe.
