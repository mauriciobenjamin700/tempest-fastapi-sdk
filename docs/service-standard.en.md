# Service standard

This page is the **citable reference** for how a Tempest service is laid
out: each item of the standard, why it exists, and **what enforces it
today** — a `tempest lint-layers` code, a `tempest check` step, or a test
`tempest new` already writes into your project. When an item has no gate,
the page says so too.

The standard is the `transport-backend`'s, the service these rules were
measured on. [#718](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/718)
records why it became the reference: a new service was written by an AI
**without starting from `tempest new`**, and only a review against the
transport brought it back to a clean `lint-layers --strict`,
`lint-schemas` and `openapi-errors`.

!!! tip "Start from `tempest new`"
    Everything this page describes already comes out of `tempest new`.
    Writing the service by hand, or copying another one, is exactly the
    mistake of #718. For a new domain inside an existing project, follow
    the recipe in the `CLAUDE.md` the scaffold writes; for the file-shape
    rules, see the [Recommended architecture](recommended-architecture.md).

## What `tempest new` generates

!!! info "The `tempest` command needs the `[cli]` extra (or `[cli-ops]` on a server)"
    Without either, `tempest` exits with code 2 and prints how to install it.
    In development: `uv add --dev "tempest-fastapi-sdk[cli]"`. Details in
    [Installation → CLI](installation.md#cli).

```bash
tempest new demo --extras sqlite
```

The project's directory tree (`tree -d`, measured on this page's branch):

```text
.
├── alembic
│   └── versions
├── src
│   ├── api
│   │   ├── dependencies
│   │   ├── docs
│   │   └── routers
│   ├── controllers
│   │   └── system
│   ├── core
│   │   ├── constants
│   │   │   └── messages
│   │   ├── enums
│   │   ├── exceptions
│   │   └── types
│   ├── db
│   │   ├── configs
│   │   ├── models
│   │   └── repositories
│   ├── schemas
│   ├── scripts
│   ├── services
│   └── utils
└── tests
    ├── integration
    │   └── system
    └── unit
        ├── architecture
        └── core
```

And `tempest check` on it, untouched (`ENV=test`, which the generated
`conftest.py` pins as well):

```text
$ ruff check --extend-select ANN001,ANN201,ANN202,ANN205,ANN206 .
All checks passed!
$ ruff format --check .
61 files already formatted
$ mypy --disallow-untyped-defs --disallow-incomplete-defs .
Success: no issues found in 59 source files
$ lint-layers
Layer rules: no findings.
$ openapi-errors
Every route's declared errors match its flow (src).
$ lint-schemas
Schema metadata: src.schemas is fully documented.
$ pytest -n auto -p no:cacheprovider
...
9 passed
```

The nine tests are the ones the scaffold writes: three integration tests
of the root route and health, three architecture guards and three for the
exception contract.

## The standard, item by item

| Rule | What it looks like | What enforces it today |
| --- | --- | --- |
| One layer class per file, per context | `src/<layer>/<context>/<file>.py`, only the class + `logger` + `__all__` | `LAY001`, `LAY002` |
| A file name without the layer suffix | `services/orders/checkout.py` → `CheckoutService` (never `checkout_service.py`) | `LAY003` |
| At most 1000 lines per file, code and tests, no exception list | a big service becomes several by responsibility; a big test file, one file per theme | `SIZE001` |
| No nested `def` and no module-level `_x`, in `src` and `tests` | a stateful callback becomes a callable object | `STR001`, `STR002` |
| `__init__` imports relatively; elsewhere, import the package | `from .x import Y` / `from src.services import X` | `IMP001`, `IMP002` |
| `__init__.py` only re-exports | `from .x import Y as Y` + `__all__`; assembly in `router.py`, `factory.py`, `runner.py` | `INIT001` |
| A resource is a process singleton in one place | `api/dependencies/resources.py` (`db`, `get_session`, `get_*`) | — (scaffold convention) |
| Providers per layer; a router never sees `AsyncSession` | `api/dependencies/controllers.py` calls the `build_*` | `RTR001`, `DI001`–`DI003` |
| A controller inherits the SDK's base | `BaseController[Service]` (one service) or `Controller` (several, or none) | — (scaffold convention) |
| Login and signup come from the SDK | `UserAuthService` + `make_auth_router(..., prefix="/api/auth")` | — (no gate) |
| `/api` set in one place | `routers/api.py` → `build_api_router()` | `RTR003`, `RTR004` |
| A domain router is a `TempestAPIRouter` | `TempestAPIRouter(prefix=...)`, never `fastapi.APIRouter` | `RTR005` |
| OpenAPI text out of the router | `api/docs/<router>.py`, one `EndpointDoc` per handler | `RTR006` |
| Exceptions: one module per status, contract in the class body | `core/exceptions/{bad_request,unauthorized,forbidden,not_found,conflict}.py` | `tests/unit/core/test_exceptions_contract.py` |
| Frozen exception contract | status, `code`, message and `field` of each class | `tests/unit/core/test_exceptions_contract.py` |
| Prose and constants out of the module that uses them | `core/constants/` + `core/constants/messages/{errors,successes}.py` | — (scaffold convention) |
| `Protocol`, alias, `NamedTuple`, enum | `core/types/<domain>.py`, `core/enums/<domain>.py` | `LAY001` (outside the layer); `ENUM001` (suffix) |
| Database | `db/configs/{connection,migrations,names}.py` | — (scaffold convention) |
| Mapping in the repository | `map_to_response` | `REPO001` (partial) |
| Graph assembled outside the request | `scripts/composition.py` (`build_*`) | `DI003` (`from_session`) |
| Every schema field documented | `Field(title=..., description=..., examples=...)` | `lint-schemas` + `tests/unit/architecture/test_schemas.py` |
| Every route declares what it raises | `@raises(...)` below the route decorator | `openapi-errors` + `tests/unit/architecture/test_openapi_errors.py` |
| Tests | `Cenário:`/`Esperado:`, checking something; `tests/{unit,integration}/<area>/` | `TST001`–`TST004` |
| Guards in the suite itself | `tests/unit/architecture/` | `test_layers.py`, `test_openapi_errors.py`, `test_schemas.py` |
| Warnings fail from the first commit | `layers_strict = true` under `[tool.tempest]` | `tempest check` |

The `LAY`, `STR`, `IMP`, `INIT`, `SIZE`, `DI`, `RTR003`–`RTR006` and `TST` codes are
**warnings**: they only fail `tempest check` with `layers_strict = true`,
which the generated `pyproject.toml` already turns on. The table of every
code, with each one's real output, is in
[CLI › Layer rules](recipes/cli.md#layer-rules-tempest-lint-layers).

!!! note "What has no gate"
    The resource singleton, `core/constants` and `db/configs` are
    convention: the scaffold starts that way and the `CLAUDE.md`
    recipe teaches it, but no rule fails when you deviate. They were left
    out because the check would have to judge intent (what is "prose",
    what is a "process resource"), and a rule that broad flags correct
    code.

    The controller base and the auth flow have no gate either.
    `Controller` has no methods at all, so inheriting it changes nothing
    that runs; what the convention protects — a controller receives
    services, never a session or another controller — is already
    `DI001`/`DI002`. And a hand-written `/login` route cannot be told,
    from the syntax, from one that has a reason to exist (a proxy to
    another provider): the gate would have to judge intent again.

## `__init__.py` only re-exports

**Why:** an `__init__.py` runs on the import of any module of its package.
A function, a composed router or a factory in there runs for whoever only
wanted a schema, sits outside the file index (nobody looks for
`build_admin_router` in an `__init__.py`) and invites an import cycle. The
package holds a docstring, relative re-exports and `__all__` — nothing
else.

❌ The package builds the router:

```python
# src/api/routers/admin/__init__.py
from fastapi import APIRouter

from .orders import router as orders_router

admin_router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])
admin_router.include_router(orders_router)
```

✅ `router.py` builds it, the `__init__.py` re-exports it, and
`build_api_router` includes `build_admin_router()`:

```python
# src/api/routers/admin/router.py
from tempest_fastapi_sdk import TempestAPIRouter

from .orders import router as orders_router


def build_admin_router() -> TempestAPIRouter:
    """Assemble the admin routers under ``/admin``.

    Returns:
        TempestAPIRouter: ``/admin`` with every admin router included.
    """
    admin: TempestAPIRouter = TempestAPIRouter(prefix="/admin", tags=["admin"])
    admin.include_router(orders_router)
    return admin
```

```python
# src/api/routers/admin/__init__.py
from .router import build_admin_router as build_admin_router

__all__: list[str] = ["build_admin_router"]
```

A factory goes to `factory.py` (`src/integrations/payment/factory.py`), a
seed run to `runner.py` (`src/db/seed/runner.py`), each re-exported the
same way. `INIT001` reports every statement that does more than re-export
— a `def`, a `class`, a call, a constant —, in the source and in the suite;
`if TYPE_CHECKING:` and an optional import in a `try` pass while they only
import.

## Authentication: mount the SDK's, do not write yours

**Why:** signup, activation, login, refresh, logout, `/me`, password and
email change already exist in the SDK, with the password policy, the
duplicate-email 409, session revocation and the JWT pair right. A
hand-written `AuthService` rebuilds each of those and drifts one release at
a time.

The service composes the pieces, all exported by `tempest_fastapi_sdk`:
`BaseUserModel` (the base of `UserModel`), `make_user_token_model(...)` and
`make_user_refresh_token_model(...)` (the token tables), `AuthSettings` (in
`Settings`, next to `JWTSettings`), `UserAuthService`,
`make_auth_router(...)` and, on the service's own routes,
`UserAuthService.current_user_dependency(...)` or
`make_jwt_user_dependency`.

❌ Hand-written auth routes:

```python
# src/api/routers/auth.py
from fastapi import APIRouter, Depends

from src.api.dependencies import get_auth_controller
from src.controllers import AuthController
from src.schemas import LoginSchema, TokenPairSchema

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    data: LoginSchema,
    controller: AuthController = Depends(get_auth_controller),
) -> TokenPairSchema:
    """Log a user in by email and password."""
    return await controller.login(data)
```

✅ The service comes out of the composition root and the router is mounted
in `create_app`, like the other SDK routers:

```python
# src/scripts/composition.py
from tempest_fastapi_sdk import AsyncDatabaseManager, UserAuthService

from src.core import Settings
from src.db.models import UserModel, UserTokenModel


def build_auth_service(db: AsyncDatabaseManager, settings: Settings) -> UserAuthService:
    """Assemble the SDK's auth service over this project's tables.

    Args:
        db (AsyncDatabaseManager): The process-wide database manager.
        settings (Settings): Settings composing ``AuthSettings`` and
            ``JWTSettings``.

    Returns:
        UserAuthService: The service ``make_auth_router`` serves.
    """
    return UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
        email=None,
    )
```

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import make_auth_router

from src.api.dependencies import db, get_session
from src.core import settings
from src.scripts import build_auth_service


def create_app() -> FastAPI:
    """Build the application with the SDK's auth routes under ``/api/auth``.

    Returns:
        FastAPI: The configured application.
    """
    app = FastAPI()
    app.include_router(
        make_auth_router(
            build_auth_service(db, settings),
            session_factory=get_session,
            prefix="/api/auth",
        ),
    )
    return app
```

To extend it without rewriting a route: `signup_schema=` (a `SignupSchema`
subclass; a field that is a column lands on the row before the insert),
`on_signup=` (a `(session, user, payload)` hook inside the insert's
transaction) and `me_response_model=` (an `AuthUserSchema` subclass,
returned by `GET /auth/me`). The three appear together in one complete
example in the
[auth recipe](recipes/auth-flow/endpoints.md#an-account-born-with-more-than-email-and-password-v02780).

!!! warning "The whole prefix goes in `prefix=`, with the router mounted on the app"
    `make_auth_router` writes its own prefix into the refresh cookie's
    `Path` and into the URLs it builds, and it cannot see a prefix put
    around it. Measured with `AUTH_TOKEN_DELIVERY=cookie`, a login followed
    by `POST /api/auth/refresh` with the client's cookie jar:

    | `make_auth_router(...)` | `POST /api/auth/login` | refresh cookie | `POST /api/auth/refresh` |
    | --- | --- | --- | --- |
    | default `/auth`, included in an `APIRouter(prefix="/api")` | `200` | `Path=/auth` | `401` |
    | `prefix="/api/auth"`, `app.include_router(...)` | `200` | `Path=/api/auth` | `200` |

    Included inside `build_api_router` with the default `/auth`, the route
    answers at `/api/auth/login`, but the cookie goes out with `Path=/auth`
    and the browser never sends it to `/api/auth/refresh`. Mounting on the
    app with `prefix="/api/auth"` does not trip `RTR004`, which reads only
    the `prefix=` of `APIRouter(...)` and `include_router(...)` — measured
    with `tempest lint-layers` over a `tempest new --extras auth` project
    with that mount in `create_app`.

## Exceptions: one module per status, contract in the body

**Why:** the status + `code` pair is what the client reads to decide what
to show, and the message is what the user sees. When the class declares
all three in its body, whoever reads the class **without instantiating
it** — `error_responses` documenting OpenAPI, the `BaseRepository` raising
the 404, `tempest openapi-errors` — sees the same contract the client
receives. An `__init__` that builds the message hides it, and makes every
`raise` remember the arguments.

The module says the status, so whoever looks for "what returns 409" opens
`conflict.py`:

```python
# src/core/exceptions/not_found.py
from typing import Any, ClassVar

from tempest_fastapi_sdk import NotFoundException

from src.core.constants.messages import errors


class ItemNotFoundException(NotFoundException):
    """Raised when an item id matches no stored record."""

    code = "ITEM_NOT_FOUND"
    message = errors.ITEM_NOT_FOUND
    field = "item_id"
    details_example: ClassVar[dict[str, Any]] = {"item_id": "3f2b8c1e"}
```

- `code` is the stable identifier. Without it the class inherits the
  SDK's generic one (`"NOT_FOUND"`), and the SDK warns with
  `InheritedErrorCodeWarning`.
- `message` comes from a constant, never from an f-string.
- `field` names the culprit input. Leave it unset on invalid credentials:
  saying which half of the pair was wrong helps whoever is guessing.
- Data that changes per `raise` goes in `details=`:

```python
from src.core.exceptions import ItemNotFoundException


def require_item(item_id: str, found: bool) -> None:
    """Refuse a lookup that missed.

    Args:
        item_id (str): The id that was looked up.
        found (bool): Whether the lookup matched a row.

    Raises:
        ItemNotFoundException: When ``found`` is false.
    """
    if not found:
        raise ItemNotFoundException(details={"item_id": item_id})
```

### The frozen contract

The generated `tests/unit/core/test_exceptions_contract.py` keeps, as
literals, the status, `code`, message and `field` of every class
`src.core.exceptions` re-exports — and fails when a new class lands
without an entry. Measured: with an `OrderAlreadyPaidException`
re-exported and no line in the contract, the test fails like this:

```text
E       AssertionError: assert {'ItemNotFoun...aidException'} == {'ItemNotFoundException'}
E         Extra items in the left set:
E         'OrderAlreadyPaidException'
1 failed, 2 passed
```

Renaming, moving or rewording an exception becomes a visible change in
that file's diff — which is an API change.

## Messages and constants out of the module that uses them

**Why:** a message string spread over a service and an exception is the
first place an AI (and we) duplicate text. With one constant per `code`,
a wording change is one line, and the reader sees where the text came
from:

```python
# src/core/constants/messages/errors.py
ITEM_NOT_FOUND: str = "Item not found."
"""Message of ``ItemNotFoundException``."""

__all__: list[str] = ["ITEM_NOT_FOUND"]
```

Import the **module**, not the constant —
`from src.core.constants.messages import errors` — so the use site reads
`errors.ITEM_NOT_FOUND`. A constant that is not client text goes in
`core/constants/<domain>.py`, re-exported by the package. An enum goes in
`core/enums/<domain>.py`; a `Protocol`, an alias and a result
`NamedTuple`, in `core/types/<domain>.py` — never next to the class in a
layer module, which holds that class alone (`LAY001`).

## Database: `db/configs`

**Why:** the engine configuration and the boot migration are decisions
for the whole process, and each has **one** place:

```python
# src/db/configs/connection.py
from tempest_fastapi_sdk import AsyncDatabaseManager, DatabaseSettings


def make_db_manager(settings: DatabaseSettings) -> AsyncDatabaseManager:
    """Build the database manager for the given settings.

    Args:
        settings (DatabaseSettings): The project's settings.

    Returns:
        AsyncDatabaseManager: A manager with the bind parameters out of
        the error messages.
    """
    return AsyncDatabaseManager(**settings.database_kwargs(), hide_parameters=True)
```

`hide_parameters=True` keeps the bind parameters out of the text of every
SQLAlchemy exception. Without it, a logged `IntegrityError` writes
`[parameters: (...)]` with the row's e-mail, document number or password
hash — the same defect #718 found in `tempest user create`.

`db/configs/migrations.py` holds `run_migrations`, which the lifespan
calls while `DATABASE_MIGRATE_ON_STARTUP` is on (the default). It uses
`AlembicHelper.sync_schema_async`, which covers an empty database, one
created before Alembic and one already migrated, and refuses a pending
migration that drops a table, column or constraint. Turn it off when
several replicas start together and a job runs `tempest db upgrade`.

!!! info "Neither imports the project"
    `src.db.models` imports `src.db.configs` for the table names. If
    `connection.py` imported `src.core`, loading the models (which
    Alembic does on every revision) would build the settings. That is why
    `make_db_manager` takes the settings as a parameter, typed with the
    SDK's `DatabaseSettings` mixin, and `run_migrations` takes the URL.

## OpenAPI text out of the router

**Why:** the router is wiring — method, path, `Depends`, `responses=`. The
prose Swagger shows changes for other reasons, and mixed into the
decorator it gets lost in the wiring's diff. `api/docs/<router>.py` holds
one `EndpointDoc` per handler, in `UPPER_SNAKE`:

```python
# src/api/docs/base.py
from typing import TypedDict


class EndpointDoc(TypedDict):
    """Summary and description of one endpoint in the OpenAPI document."""

    summary: str
    description: str
```

```python
# src/api/docs/service_info.py
from .base import EndpointDoc

INDEX: EndpointDoc = {
    "summary": "Describe the service",
    "description": (
        "Returns the name the service runs under, read from the TITLE "
        "setting. Public, no authentication."
    ),
}
```

And the router spreads the dictionary into the decorator:

```python
# src/api/routers/service_info.py
from fastapi import Depends
from tempest_fastapi_sdk import TempestAPIRouter

from src.api.dependencies import get_service_info_controller
from src.api.docs import service_info
from src.controllers import ServiceInfoController
from src.schemas import ServiceInfoSchema

router: TempestAPIRouter = TempestAPIRouter(tags=["root"])


@router.get("/", **service_info.INDEX)
async def index(
    controller: ServiceInfoController = Depends(get_service_info_controller),
) -> ServiceInfoSchema:
    """Placeholder root endpoint returning the service name.

    Args:
        controller (ServiceInfoController): The injected controller.

    Returns:
        ServiceInfoSchema: The service title.
    """
    return await controller.describe()
```

Both keys are the `summary` and `description` parameters of
`APIRouter.get`/`post`, so mypy checks the spread.

The wrong shape is what shows up when the text stays in the router: loose
`SUMMARY_*` constants at the top of the module, or `summary="..."` written
in the decorator. `tempest check` reports each keyword as `RTR006` — and,
in the same file, the `fastapi.APIRouter` as `RTR005` (see the next
section):

```python
# src/api/routers/service_info.py
from fastapi import APIRouter, Depends

from src.api.dependencies import get_service_info_controller
from src.controllers import ServiceInfoController
from src.schemas import ServiceInfoSchema

SUMMARY_INDEX: str = "Describe the service"

router: APIRouter = APIRouter(tags=["root"])


@router.get("/", summary=SUMMARY_INDEX, description="Returns the service name.")
async def index(
    controller: ServiceInfoController = Depends(get_service_info_controller),
) -> ServiceInfoSchema:
    """Placeholder root endpoint returning the service name.

    Args:
        controller (ServiceInfoController): The injected controller.

    Returns:
        ServiceInfoSchema: The service title.
    """
    return await controller.describe()
```

```text
$ tempest lint-layers . --rule RTR005 --rule RTR006
src/api/routers/service_info.py:10: RTR005 (warning) APIRouter(...) builds this router (`router`); build it with `TempestAPIRouter` from tempest_fastapi_sdk, which turns each handler's `@raises(...)` into its `responses=` (a plain APIRouter ignores the tag).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `summary=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `description=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
3 layer finding(s): 0 error(s), 3 warning(s).
```

Same text, another place: move each pair into an `EndpointDoc` in
`api/docs/service_info.py` and spread it into the decorator, as above.

??? info "Why `EndpointDoc` lives in the template and not in the SDK"
    The SDK already has `RouteDoc` (in `tempest_fastapi_sdk.api.route_docs`),
    but it is the bilingual catalog of the **SDK's own** routes: it
    accepts `None` to fall back to the function name, and is read by key.
    The service's `EndpointDoc` is a two-field `TypedDict` with both keys
    required, spread straight into the decorator. Publishing it from the
    SDK would be public surface for a two-line type each service reads
    from its own `api/docs` — the kind of wrapper we prefer not to depend
    on.

## Route errors: `TempestAPIRouter` + `@raises`

**Why:** the client needs to know, from the OpenAPI document, which errors
a route returns — with each one's `code`. The list lives next to the
handler, in a `@raises(...)` right **below** the route decorator, and the
SDK's `TempestAPIRouter` turns it into the route's `responses=`. Every
domain router is a `TempestAPIRouter`, never a `fastapi.APIRouter`:

```python
# src/api/routers/product.py
from fastapi import Depends, status
from tempest_fastapi_sdk import TempestAPIRouter, raises

from src.api.dependencies import get_product_controller
from src.api.docs import product as product_docs
from src.controllers import ProductController
from src.core.exceptions import ProductNameTakenException, ProductSkuTakenException
from src.schemas import ProductCreateSchema, ProductResponseSchema

router: TempestAPIRouter = TempestAPIRouter(prefix="/products", tags=["products"])


@router.post("", status_code=status.HTTP_201_CREATED, **product_docs.CREATE_PRODUCT)
@raises(ProductNameTakenException, ProductSkuTakenException)
async def create_product(
    data: ProductCreateSchema,
    controller: ProductController = Depends(get_product_controller),
) -> ProductResponseSchema:
    """Create a product.

    Args:
        data (ProductCreateSchema): The creation payload.
        controller (ProductController): The injected controller.

    Returns:
        ProductResponseSchema: The created product.
    """
    return await controller.create(data)
```

That route's `409` comes out like this in `app.openapi()["paths"]["/api/products"]["post"]["responses"]`
(measured with `fastapi` 0.165.0, the SDK's floor, and both exceptions
declaring `code`, `message` and a docstring in the class body):

```json
{
  "description": "PRODUCT_NAME_TAKEN | PRODUCT_SKU_TAKEN",
  "content": {
    "application/json": {
      "schema": {
        "$ref": "#/components/schemas/ErrorResponseSchema"
      },
      "examples": {
        "PRODUCT_NAME_TAKEN": {
          "summary": "Name in use.",
          "value": {
            "detail": "A product with this name already exists.",
            "code": "PRODUCT_NAME_TAKEN",
            "details": {},
            "field": "name"
          }
        },
        "PRODUCT_SKU_TAKEN": {
          "summary": "SKU in use.",
          "value": {
            "detail": "A product with this SKU already exists.",
            "code": "PRODUCT_SKU_TAKEN",
            "details": {}
          }
        }
      }
    }
  }
}
```

`ErrorResponseSchema` lands in `components.schemas` and the response
points at it through `$ref`; exceptions sharing a status become one
example each, keyed by `code`.

Three details, all measured on the same app:

- **On a `fastapi.APIRouter`, `@raises` does nothing.** The tag stays on
  the handler, nobody reads it, and the route comes out with only `200`
  and `422` — no error, no warning. That is why `tempest check` reports an
  `APIRouter` in `routers/` as `RTR005`.
- **An explicit `responses=` is for overriding one status.** The
  hand-written entry replaces the generated one for that status code
  **whole** — the `$ref` and that status's examples are gone. Use it only
  when you really want other text for one code.
- **The aggregator stays an `APIRouter`.** The responses are built by the
  router the handler is registered on, and `include_router` carries them
  up unchanged. A `TempestAPIRouter` in `build_api_router` adds nothing,
  and a domain `APIRouter` included into a `TempestAPIRouter` aggregator
  still has no errors.

`tempest check` (the `openapi-errors` step) reads `@raises` as the route's
declaration: an exception the flow reaches that is missing from the list
fails the gate. The full recipe, with the message catalog and
`error_responses` for routers that are not yours, is in
[OpenAPI errors](recipes/openapi-errors.md).

## Guards in the suite itself

**Why:** `tempest check` already runs `lint-layers`, `openapi-errors` and
`lint-schemas`, but not everyone runs `tempest check` — the editor runs
`pytest`, a pre-commit hook runs `pytest`. The scaffold writes the three
gates as tests too, in `tests/unit/architecture/`:

```python
# tests/unit/architecture/test_layers.py
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import lint_layers

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


def test_the_layer_rules_report_nothing() -> None:
    """Cenário: as regras de camada sobre o projeto inteiro.

    Esperado: nenhum achado, nem erro nem aviso.
    """
    findings = [finding.render(PROJECT_ROOT) for finding in lint_layers(PROJECT_ROOT)]
    assert findings == []
```

`test_openapi_errors.py` does the same with `analyze_paths` over `src`,
and `test_schemas.py` with `assert_schema_metadata(src.schemas)`. The
layer test fails on **warnings** too — the same bar as `layers_strict`.

The suite is split by kind and area: `tests/unit/<area>/` for one class in
isolation, `tests/integration/<area>/` for a request through the app, each
directory with an `__init__.py`.

## An existing project: `tempest generate --standard`

`tempest new` only writes new projects. A service generated before this
version keeps its `core/exceptions.py`, `tests/test_smoke.py` and the
`AlembicHelper` in the lifespan — and keeps working. To bring in the
pieces of the standard that collide with nothing, run at the project
root:

```bash
tempest generate --standard
```

On a service whose `src/core/exceptions.py` holds `UserNotFoundError`
(`NotFoundException`) and `UserEmailAlreadyTakenError`
(`ConflictException`), the output is:

```text
  + src/core/constants/__init__.py
  + src/core/constants/messages/__init__.py
  + src/core/constants/messages/errors.py
  + src/core/constants/messages/successes.py
  + src/core/enums/__init__.py
  + src/core/types/__init__.py
  + tests/unit/__init__.py
  + tests/unit/architecture/__init__.py
  + tests/unit/architecture/test_layers.py
  + tests/unit/architecture/test_openapi_errors.py
  + tests/unit/architecture/test_schemas.py
  + tests/unit/core/__init__.py
  + src/core/exceptions/conflict.py
  + src/core/exceptions/not_found.py
  + src/core/exceptions/__init__.py
  + tests/unit/core/test_exceptions_contract.py
  - src/core/exceptions.py
  > UserEmailAlreadyTakenError -> core/exceptions/conflict.py
  > UserNotFoundError -> core/exceptions/not_found.py
Standard: 16 written, 0 skipped, 2 exception(s) moved.
```

What happens:

- **Missing packages and guards** (`core/constants/`, `core/enums/`,
  `core/types/`, `tests/unit/architecture/`) come from the same templates
  as `tempest new`. A file that already exists is left alone unless you
  pass `--force`; and a package whose flat module already exists
  (`core/constants.py`) is **not** written, because the new package would
  shadow the module silently — the report says why.
- **`core/exceptions.py` becomes the `core/exceptions/` package**, one
  module per status. Each class's status is read by **importing** the
  module, not guessed from its base: a class that inherits a 500 base and
  declares `status_code = 409` goes to `conflict.py`. The `__init__.py`
  re-exports everything, so `from src.core.exceptions import
  UserNotFoundError` keeps working, and the flat file is deleted.
- **The contract is frozen** in `tests/unit/core/test_exceptions_contract.py`
  with the status, `code`, message and `field` each class has today. A
  class that builds its message in `__init__` gets a warning naming it:
  the contract fails for it until the data moves into the class body and
  what changes per `raise` goes in `details=`.

!!! warning "The migration refuses before writing"
    If `core/exceptions.py` holds anything besides imports, classes and
    `__all__` (a module-level constant or function has no status to go
    to), if splitting by status would make the modules import each other,
    or if the module does not import, the command exits with code 1, the
    message names the file and the line, and **nothing** is written.

Running it again changes nothing: everything that exists is skipped. The
rest of the standard (`db/configs`, `scripts/composition.py`,
`api/docs/`) touches the app's wiring and stays manual — one item per PR,
in the order of the
[recommended architecture's migration](recommended-architecture.md#migrating-an-existing-service).
Turn `layers_strict = true` on once the warnings reach zero.

## Recap

- **Start from `tempest new`**; a new domain follows the recipe of the
  generated `CLAUDE.md`.
- **Exception:** one module per status, `code`/`message`/`field` in the
  body, no `__init__`, per-raise data in `details=` — and one line in the
  frozen contract.
- **Client text in `core/constants/messages`**; enums, types and
  constants in per-domain packages.
- **`db/configs`** holds `make_db_manager` (`hide_parameters=True`) and
  the boot's `run_migrations`.
- **`api/docs`** holds each handler's `EndpointDoc`.
- **`__init__.py` only re-exports**; composition in `router.py`, a
  factory in `factory.py`, a seed in `runner.py`. A file named without
  the layer suffix, at most 1000 lines.
- **Auth is the SDK's**: `UserAuthService` + `make_auth_router(..., prefix="/api/auth")`
  mounted on the app, extended through `signup_schema`, `on_signup` and
  `me_response_model`.
- **`layers_strict = true`** and the guards in `tests/unit/architecture/`
  make `tempest check` and `pytest` fail on the first deviation.
