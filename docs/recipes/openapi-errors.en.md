# Errors in OpenAPI (Swagger / ReDoc)

The SDK serializes **every** `AppException` into a single envelope —
`{detail, code, details}`. Great for the client... as long as it knows **which**
`code` values to expect. And that was exactly the hole: none of it showed up in
the OpenAPI schema.

This recipe closes the hole in four steps. The first two already solve the
frontend's problem; the last two are ergonomics and drift protection. 🚀

## The problem, measured

Take a real route that raises six exceptions:

```python
# src/api/routers/jobs.py
from uuid import UUID

from fastapi import APIRouter

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    CandidateDoesNotHaveCoinsException,
    CategoryNotFoundException,
    ServiceFullException,
    ServiceNotFoundException,
    ServiceOwnerCannotApplyException,
)
from src.schemas import CandidateResponseSchema

router: APIRouter = APIRouter(prefix="/api/jobs")


@router.post("/{service_id}/candidates", status_code=201)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Apply the authenticated user to a service."""
    raise NotImplementedError
```

Ask OpenAPI what that route returns:

```pycon
>>> spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"].keys()
dict_keys(['201', '422'])
```

Two statuses. But the real flow produces **four**:

| Status | `code` | Exception |
| --- | --- | --- |
| 404 | `SERVICE_NOT_FOUND` | `ServiceNotFoundException` |
| 404 | `CATEGORY_NOT_FOUND` | `CategoryNotFoundException` |
| 403 | `SERVICE_OWNER_CANNOT_APPLY` | `ServiceOwnerCannotApplyException` |
| 409 | `SERVICE_FULL` | `ServiceFullException` |
| 409 | `CANDIDATE_ALREADY_EXISTS` | `CandidateAlreadyExistsException` |
| 400 | `CANDIDATE_DOES_NOT_HAVE_COINS` | `CandidateDoesNotHaveCoinsException` |

Note the pairs: **two 404s and two 409s**. Documenting only the status is not
enough — the frontend needs the `code` to pick the message and the recovery
action.

!!! danger "The practical cost"
    Without the codes in the schema, the generated client has no error enum, the
    frontend writes `if (res.status === 409)` unaware that there are two 409s
    with different recoveries, and a new error code is discovered in production.

## Step 1 — declare `code` in the class body

Before any tooling, one prerequisite: the `code` must be **readable without
instantiating the exception**.

The SDK accepts `code=` at the raise site, and it works identically at runtime.
But it hides the real value from every static reader:

```python
# ⚠️ works, but the `code` is invisible to introspection
class CategoryInUseException(ConflictException):
    """Category still referenced by services."""


raise CategoryInUseException("...", code="CATEGORY_IN_USE")
```

```pycon
>>> CategoryInUseException.code            # class attribute
'CONFLICT'
>>> CategoryInUseException("x").code       # instance
'CATEGORY_IN_USE'
```

Reading the right value would require **instantiating**, and instantiating
requires knowing each `__init__` signature — which varies. So no tool can build
`responses` from the classes.

The class-attribute form already works today and is introspectable:

```python
# src/core/exceptions.py
from typing import Any, ClassVar
from uuid import UUID

from tempest_fastapi_sdk import ConflictException


class CategoryInUseException(ConflictException):
    """Category still referenced by services."""

    code: str = "CATEGORY_IN_USE"
    details_example: ClassVar[dict[str, Any]] = {
        "category_id": "8f2c1e40-0000-4000-8000-000000000000"
    }

    def __init__(self, category_id: UUID | str) -> None:
        """Initialize the exception.

        Args:
            category_id (UUID | str): The category that cannot be deleted.
        """
        super().__init__(
            message="Cannot delete a category that still has services.",
            details={"category_id": str(category_id)},
        )
```

```pycon
>>> CategoryInUseException.code, CategoryInUseException.status_code
('CATEGORY_IN_USE', 409)
```

`code` (and `status_code`, when the parent's is wrong) in the class body;
`__init__` only builds `message` and `details`.

!!! tip "`details_example` is documentation-only"
    `details_example` is **never** read at runtime — it only populates the
    OpenAPI example. Declare it when the exception attaches context worth showing
    to whoever consumes the API.

    Annotate it as `ClassVar[dict[str, Any]]`: besides being what the project's
    full-typing rule asks for, that is what silences ruff's `RUF012` ("mutable
    default value for class attribute").

### The warning that catches the silent defect

As of **v0.160.0**, a subclass that declares no `code` of its own — and therefore
inherits a generic SDK one — warns at class creation:

```pycon
>>> class CategoryInUseException(ConflictException):
...     """Category still referenced by services."""
InheritedErrorCodeWarning: src.core.exceptions.CategoryInUseException declares
no `code`, so it inherits the generic ConflictException.code = 'CONFLICT'.
Clients cannot tell it apart from any other 409 response and
`error_responses()` cannot document it. Declare `code = "..."` in the class body.
```

This is a real, silent defect: in a production service one subclass shipped
`code: "CONFLICT"` for **months**, indistinguishable from any other 409 to the
client.

!!! info "When the warning does **not** fire"
    - The subclass declares `code` — the documented path.
    - The subclass declares `message_key` — it already localizes under its own
      key.
    - The inherited `code` is a **domain** one (declared by a project-owned
      ancestor). Specializing `DomainConflictException` is deliberate, not a
      defect.

If the raise-site pattern is deliberate in your project, silence it by category:

```python
import warnings

from tempest_fastapi_sdk import InheritedErrorCodeWarning

warnings.filterwarnings("ignore", category=InheritedErrorCodeWarning)
```

## Step 2 — `error_responses(*exceptions)`

Now the core. Pass the classes, get the dict FastAPI's `responses=` expects:

```python
# src/api/routers/jobs.py
from uuid import UUID

from fastapi import APIRouter
from tempest_fastapi_sdk import error_responses

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    CandidateDoesNotHaveCoinsException,
    CategoryNotFoundException,
    ServiceFullException,
    ServiceNotFoundException,
    ServiceOwnerCannotApplyException,
)
from src.schemas import CandidateResponseSchema

router: APIRouter = APIRouter(prefix="/api/jobs")


@router.post(
    "/{service_id}/candidates",
    status_code=201,
    responses=error_responses(
        ServiceNotFoundException,
        CategoryNotFoundException,
        ServiceOwnerCannotApplyException,
        ServiceFullException,
        CandidateAlreadyExistsException,
        CandidateDoesNotHaveCoinsException,
    ),
)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Apply the authenticated user to a service."""
    raise NotImplementedError
```

Ask OpenAPI again:

```pycon
>>> spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"].keys()
dict_keys(['201', '400', '403', '404', '409', '422'])
```

All four statuses showed up. And the two 404s stay distinguishable:

```pycon
>>> resp = spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"]
>>> resp["404"]["description"]
'SERVICE_NOT_FOUND | CATEGORY_NOT_FOUND'
>>> list(resp["404"]["content"]["application/json"]["examples"])
['SERVICE_NOT_FOUND', 'CATEGORY_NOT_FOUND']
>>> resp["404"]["content"]["application/json"]["schema"]
{'$ref': '#/components/schemas/ErrorResponseSchema'}
```

### Why `examples` and not one entry per exception

An OpenAPI restriction: **exactly one response object per status code**. With two
404s on the same endpoint there is no way to emit one entry per exception. So the
helper **groups by status** and distinguishes the codes through `examples`:

```json
{
  "404": {
    "description": "SERVICE_NOT_FOUND | CATEGORY_NOT_FOUND",
    "content": {
      "application/json": {
        "schema": {"$ref": "#/components/schemas/ErrorResponseSchema"},
        "examples": {
          "SERVICE_NOT_FOUND": {
            "summary": "Service does not exist.",
            "value": {"detail": "...", "code": "SERVICE_NOT_FOUND", "details": {}}
          },
          "CATEGORY_NOT_FOUND": {
            "summary": "Category does not exist.",
            "value": {"detail": "...", "code": "CATEGORY_NOT_FOUND", "details": {}}
          }
        }
      }
    }
  }
}
```

Swagger UI and ReDoc render that map as a **selector** — the frontend sees the
codes side by side with each one's payload. ✅

!!! check "No text typed twice"
    `summary` comes from the class `__doc__` (which the project convention
    already requires), and `detail` comes from the class `message` — or from a
    `MessageCatalog`, if you pass one.

### `ErrorResponseSchema`

Every entry's `model` is
[`ErrorResponseSchema`](../reference.md), the envelope the SDK's handlers
actually emit:

```python
from typing import Any

from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class ErrorResponseSchema(BaseSchema):
    """The JSON body the SDK's handlers emit on any failure."""

    detail: str = Field(description="Human-readable message. Localized with a catalog.")
    code: str = Field(description="Stable identifier. Branch on this.")
    details: dict[str, Any] = Field(default_factory=dict)
```

It did not exist before — anyone wanting to declare `responses={409: ...}` by
hand had nothing to point at and had to retype the shape inline on every route.

!!! warning "Branch on `code`, never on `detail`"
    `detail` changes with the request's negotiated locale when a
    `MessageCatalog` is registered. `code` is the stable contract.

### Localizing the examples

By default `error_responses` does **not** localize — it uses the class `message`,
so the generated spec never picks a language implicitly. Pass a catalog when you
want to:

```python
from tempest_fastapi_sdk import default_message_catalog, error_responses

CATALOG = default_message_catalog().merge(
    {
        "pt-BR": {"SERVICE_NOT_FOUND": "Serviço não encontrado"},
        "en-US": {"SERVICE_NOT_FOUND": "Service not found"},
    }
)

responses = error_responses(
    ServiceNotFoundException,
    catalog=CATALOG,
    locale="en-US",
)
```

A partial catalog degrades to the class `message` instead of blanking the
example — the same fallback the runtime handler uses.

### Tweaking the description

```python
responses = error_responses(
    ServiceFullException,
    CandidateAlreadyExistsException,
    descriptions={409: "The user cannot apply right now"},
)
```

Unlisted statuses keep the generated `"CODE_A | CODE_B"` summary.

## Step 3 — `@raises(...)` + `TempestAPIRouter`

Same information, written next to the handler instead of inside the route
decorator's argument list:

```python
# src/api/routers/jobs.py
from uuid import UUID

from tempest_fastapi_sdk import TempestAPIRouter, raises

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    ServiceFullException,
    ServiceNotFoundException,
)
from src.schemas import CandidateResponseSchema

router: TempestAPIRouter = TempestAPIRouter(prefix="/api/jobs")


@router.post("/{service_id}/candidates", status_code=201)
@raises(
    ServiceNotFoundException,
    ServiceFullException,
    CandidateAlreadyExistsException,
)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Apply the authenticated user to a service."""
    raise NotImplementedError
```

`TempestAPIRouter` is a drop-in for `fastapi.APIRouter` — same arguments, same
methods — that expands the tag into `responses=` **before** the route is
constructed. That is why the model reaches `components.schemas` as a real
`$ref`.

```python
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)   # `responses` survives the include
```

!!! warning "Decorator order"
    `@raises` must sit **below** `@router.post`, so it runs first and the route
    decorator receives an already-tagged function.

!!! danger "`@raises` on a plain `APIRouter` is inert"
    The tag is only read by `TempestAPIRouter`. On a bare `fastapi.APIRouter` it
    does nothing — use `responses=error_responses(...)` there.

An explicit `responses=` **wins** per status code, so a hand-written entry always
overrides the generated one:

```python
@router.get("/x", responses={409: {"description": "handwritten"}})
@raises(ServiceFullException, ServiceNotFoundException)
async def read() -> CandidateResponseSchema:
    """The 409 keeps the manual description; the 404 stays generated."""
    raise NotImplementedError
```

!!! question "Why explicit and not automatic?"
    The list is versioned in the diff, `mypy`/the IDE catch a class rename, and
    nothing depends on import-time heuristics. Cost: one line per exception.

## Step 4 — `tempest openapi-errors --check`

The risk of an explicit declaration is that it goes stale. Since the project
convention already requires a `Raises:` section on every docstring, this can be
verified **without runtime magic**:

```bash
tempest openapi-errors --check
```

The command walks `router → controller → service → repository` with `ast`
(without importing the application), collects each function's exceptions — from
`raise` statements **and** from `Raises:` sections — and compares them against
what each route declared:

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
src/api/routers/jobs.py:25  GET /{service_id}
  unreachable:  ServiceFullException
2 route(s) with drift, 2 undocumented exception(s).
```

- **undocumented** — reachable in the flow, absent from the route. The
  documentation hole this recipe fixes.
- **unreachable** — declared on the route, never found in the flow. An inflated
  list documenting an error that cannot happen.

It exits zero when things are in sync, so it doubles as a CI step:

```yaml
# .github/workflows/ci.yml
- name: Errors documented in OpenAPI
  run: uv run tempest openapi-errors --check
```

Options:

| Option | Effect |
| --- | --- |
| `--path DIR` | Directory (or file) to scan. Repeatable. Defaults to `./src` or `./app`. |
| `--check` | Exit non-zero on drift. Without it the report is advisory. |
| `--allow-unreachable` | With `--check`, fail only on `undocumented`. An inflated list stays a warning. |
| `--fix` | Write the missing declarations into the code. Requires a clean git tree. |
| `--dry-run` | With `--fix`, print the diff instead of writing. Runs on a dirty tree. |

!!! warning "It is a guide, not a proof"
    Two known imprecisions, both chosen to **over**-approximate rather than hide
    a hole:

    - **A call with an untyped receiver resolves by name.** `self.svc.get_by_id()`
      resolves through the **type** of `self.svc` when the attribute is
      annotated, and the search stays inside that class's hierarchy. Without an
      annotation it falls back to name resolution: two `get_by_id` methods on
      different classes become one node, so their exceptions merge. That
      inflates the reachable set (possibly clearing a genuine `unreachable`)
      instead of hiding a real hole. Annotating attributes — which the project
      convention already requires — is what buys the precision.
    - **A method inherited from outside the scanned tree is not followed**, with
      one important exception: the classes you **configure** on the base
      constructor. A `not_found_exception=CoinPackNotFoundException` in the
      repository's `super().__init__()` is attributed to the inherited methods
      that really raise it (`get`, `get_by_id`, `resolve`, `delete`,
      `soft_delete`, `restore`), following the
      controller → `service` → `repository` chain. The same holds for the
      `*_conflict_exception` kwargs. Beyond that — an inherited SDK method that
      raises no configured class — no edge is created, so declare what the base
      raises in your `Raises:` section.
    - **Dynamic raises are invisible.** `raise EXCEPTION_MAP[key]` cannot be
      resolved statically.

    !!! info "Fixed in 0.170.0"
        Before 0.170.0 **every** call resolved by name, and the route decorator
        was part of the graph — so `@router.delete(...)` registered a call to
        `delete` and reached every `delete` in the project. In a tree whose only
        `delete` was `CategoryRepository`'s, every DELETE route was reported as
        raising `CategoryInUseException`. `get` and `post` collide the same way
        wherever methods with those names exist.

    Both blind spots are covered by declaring the exception in the function's
    `Raises:` section — which the project convention already requires, and which
    the analyzer reads.

!!! tip "Point `--path` at the whole tree"
    Reachability is limited to what was scanned. Scanning only the router file
    leaves the service calls unresolved, and **every** declaration starts looking
    `unreachable`.

Call-graph analysis stays out of the runtime on purpose: it is far too fragile to
drive a response schema in production, but perfectly acceptable in a check that
exits non-zero.

## Step 5 — `--fix` writes the declarations for you

On an existing project step 4 usually points at dozens of routes. Retyping by
hand what the analyzer already knows is mechanical work — `--fix` does the
Exception → route mapping and writes the result:

```bash
tempest openapi-errors --fix --dry-run   # look at the diff first
tempest openapi-errors --fix             # write
```

On a route that declares nothing yet it injects the parameter and the imports:

```diff
+from tempest_fastapi_sdk import error_responses
+
+from src.core.exceptions import CandidateAlreadyExistsException, ServiceFullException

-@router.post("/{service_id}/candidates", status_code=201)
+@router.post(
+    "/{service_id}/candidates",
+    status_code=201,
+    responses=error_responses(
+        CandidateAlreadyExistsException, ServiceFullException
+    ),
+)
 async def apply_to_service(service_id: str) -> CandidateResponseSchema:
```

On a route that already declares some of them it **appends** to what is there —
the original order is preserved:

```diff
-    responses=error_responses(ServiceNotFoundException),
+    responses=error_responses(ServiceNotFoundException, ServiceFullException),
```

An existing `@raises(...)` is extended in place too. A route that declares
nothing, though, always gets `error_responses`, never `@raises`: `@raises` is
only read by `TempestAPIRouter`, so injecting it into a project on a plain
`APIRouter` would produce a decorator that silently does nothing — the worst
possible outcome for a tool that exists to close a documentation gap. An
existing `@raises` proves the project opted into that style, so there it is
honored.

### The three guarantees

!!! check "It only ever adds"
    `unreachable` findings are deliberately ignored. Reachability resolves by
    call name and cannot see a dynamic raise, so deleting a declaration on its
    word would remove a correct one. Pruning an inflated list stays manual.

!!! check "Edits anchored on the AST"
    Every insertion is positioned at the closing parenthesis of a call node, not
    by a regex. Nothing depends on how the decorator happens to be formatted,
    and the rest of the file — comments, layout — is untouched.

    The separating comma is derived from the code itself, via `tokenize`: a
    decorator wrapped over several lines carries a **trailing comma** (the shape
    `ruff format` produces), and prefixing another comma there would emit `,,` —
    a `SyntaxError`. Tokenizing is what makes the check trustworthy: a `,` or a
    `#` inside a string literal (`description="a, b"`) is a token of its own, so
    scanning raw text backwards would go wrong. Fixed in 0.168.3.

    The result goes
    through `ruff check --select I --fix` and `ruff format`, so a new import
    lands in sorted position and a wrapped decorator comes out formatted.

    That formatting uses **your project's config**, not ruff's defaults: the
    temporary file is created next to the file being rewritten, and ruff
    resolves its settings by walking up the directory tree. Your `line-length`
    and your `isort` sections apply, so what the command writes passes your own
    CI's `ruff format --check`.

!!! note "With no ruff, it says so instead of pretending"
    Normalization needs a ruff that actually runs — on `PATH`, importable in the
    current interpreter (`python -m ruff`), or through `uv run ruff`. Every
    candidate is probed with `--version` before being used. When none works the
    write still happens (the splice is already valid Python) and the command
    tells you what it skipped:

    ```text
    note: no working ruff found, so the new import stays where it was spliced
    and a long decorator is not wrapped. Run `tempest fix` afterwards to sort
    and format.
    ```

    Without that line you would find out from your CI's `ruff check` failing on
    a file this very command just wrote.

!!! check "A clean git tree is required"
    With a clean tree, `git diff` is the review and `git checkout` is the undo —
    the real safety net for a tool that edits code you wrote. With pending
    changes the command exits 1:

    ```text
    error: the working tree has uncommitted changes. Commit or stash them
    first — with a clean tree, `git diff` reviews what this wrote and
    `git checkout` undoes it.
    ```

    `--dry-run` is read-only, so it runs on a dirty tree without complaining.

!!! warning "An exception it cannot import is not written"
    The import is derived from the file defining the class. When the same name
    is defined in more than one file — or outside the scanned root — resolution
    is ambiguous, and writing the wrong import would break the application. That
    route is skipped and the name is reported as `unresolved`; declare that one
    by hand.

Run `--check` afterwards: the second pass should report that everything is in
sync.

## Recap

1. **Declare `code` in the class body.** It is the only introspectable form, and
   `InheritedErrorCodeWarning` tells you when you forget.
2. **`error_responses(*exceptions)`** builds `responses=` grouped by status, with
   the codes in an `examples` map and the body pointing at
   `ErrorResponseSchema`.
3. **`@raises(...)` + `TempestAPIRouter`** say the same thing next to the
   handler, without repeating the parameter.
4. **`tempest openapi-errors --check`** compares declaration against flow in both
   directions and doubles as a CI gate.
5. **`tempest openapi-errors --fix`** writes what is missing — with `--dry-run`
   to see the diff first, and requiring a clean git tree so `git checkout` is
   the undo.

With steps 1 and 2 `openapi.json` becomes the single source of truth, and the
generated client ships with the codes. 🎉
