# Permissions: RBAC, row scope and impersonation

Use to control who sees and does what: RBAC per model and action, row scope on reads and writes, and logging in as another user.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

## Granular RBAC (`access_policy=`)

By default, anyone who logs into the admin (`is_admin`) can do
everything each `AdminModel`'s `can_*` flags allow. To give an admin
access to only **some** models/actions — a "support" role that views
orders but can't delete, an "editor" that only touches content — pass an
`access_policy` to `make_admin_router`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    if user.role == "superadmin":
        return True
    if user.role == "support":
        return action is AdminPermissionEnum.VIEW       # read-only
    return admin.model is not AuditLog              # editor: everything but AuditLog


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
    ),
)
```

The policy is asked `(principal, admin, action)` for **every** action —
`VIEW` / `CREATE` / `EDIT` / `DELETE`, and `IMPERSONATE` on a model with
`impersonate=` (see [Log in as a user](#log-in-as-a-user-impersonate)). Denying yields `403`; denying
`VIEW` also **hides** the model from the dashboard and the sidebar nav,
and denying create/edit/delete hides the matching buttons. Enforced
across list, detail, create, edit, delete, bulk (delete → `DELETE`, rest
→ `EDIT`), export, import and FK autocomplete.

It also covers what one model's page shows of **another** model:

- the inline table on a parent's detail view appears only with `VIEW` on
  the child — a child with no registered `AdminModel` is asked about
  through a throwaway `AdminModel(Child)`, so a policy that decides by
  `admin.model` answers the same way;
- on an editable inline, the add row needs `CREATE` (and the child's
  `can_create`) and the delete box needs `DELETE`; a formset `POST` that
  asks for a denied action answers `403` and writes nothing;
- the FK `<select>`, the list view's FK filter and autocomplete need
  `VIEW` on the referenced model. Without it, the FK field becomes a text
  input and autocomplete answers `403`.

!!! info "Composes with the `can_*` flags"
    The policy is **additive** to the `AdminModel`'s
    `can_create`/`can_edit`/`can_delete` flags — both must allow.
    Without `access_policy`, nothing changes (every logged-in admin does
    everything), with one exception: `IMPERSONATE` only comes from a policy
    that grants it. Sync or `async`.

### One action without `EDIT` on the model (`action_policy=`) *(v0.366.0+)*

By default every custom action asks the `access_policy` for `EDIT` on the
model. On a read-only panel, granting **one** action ("Confirmar e-mail")
meant granting `EDIT` on the whole model — and, if someone turned `can_edit`
on by mistake, that same permission opened free editing of every field. Nor
could one role get one action without another.

Pass an `action_policy`: it is asked `(principal, admin, AdminAction)` for
each custom action, and the action then needs `VIEW` on the model plus its
approval, not `EDIT`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminAction,
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    """Read-only panel: VIEW on every model, nothing else."""
    return action is AdminPermissionEnum.VIEW


def action_policy(user: UserModel, admin: AdminModel, action: AdminAction) -> bool:
    """Grant only the confirm-e-mail action, only to support."""
    return user.role == "support" and action.name == "verify_email"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        action_policy=action_policy,
    ),
)
```

Measured in `tests/admin/test_custom_action_flow.py`:

- the granted action runs (`303`) and the denied one answers `403`, on the
  same model;
- the list's menu shows only the actions the policy grants — and, along the
  way, Activate / Deactivate and Delete appear only when the model enables
  them (`can_edit` + `can_toggle_active`, `can_delete`) **and** the principal
  has `EDIT` / `DELETE`; with no permitted action, the bulk form is gone;
- with `EDIT` denied, the edit form stays `403`, even with the action
  granted.

Without `action_policy`, nothing changes: a custom action needs `EDIT`, as
before.

### SQL console, tasks and logs (`surface_policy=`)

The SQL console, the task panel, the logs page and the storage browser
are not about one model, so `access_policy` has nothing to ask about. `surface_policy`
decides instead, asked `(principal, AdminSurfaceEnum)`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    AdminSurfaceEnum,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import SqlShellService

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    return user.role == "superadmin" or action is AdminPermissionEnum.VIEW


def surfaces(user: UserModel, surface: AdminSurfaceEnum) -> bool:
    return user.role == "superadmin"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        surface_policy=surfaces,
        sql_shell=SqlShellService(db),
        show_logs=True,
    ),
)
```

A denial answers `403` on every route of the surface (the console's
`POST`, task cancel, the logs export and the storage writes and `raw` route
included) and drops its sidebar
entry.

!!! warning "With `access_policy`, the surfaces are closed by default"
    With neither policy, every admin opens every surface, as before.
    With `access_policy` and **no** `surface_policy`, nobody does: the
    console reads any table the `SqlShellPolicy` allows, and an operator
    restricted to some models would read the rest through it. Pass a
    `surface_policy` to give the surfaces back to whoever should have them.

## Row scope (`row_scope=`)

`access_policy` answers yes or no for the **whole model**. Multi-tenant
needs more: company A's support may see orders, but only company A's.
Working around it with one panel per company or a hand-filtered
repository is where data leaks — one route forgetting the filter is
enough. `row_scope=` puts the filter in the panel:

```python
from typing import Any

from sqlalchemy import ColumnElement, false, true

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import TicketModel, UserModel


def own_company(user: UserModel, admin: AdminModel[Any]) -> ColumnElement[bool]:
    if user.is_superuser:
        return true()
    if user.company_id is None:
        return false()
    return TicketModel.company_id == user.company_id


site.register(AdminModel(model=TicketModel, row_scope=own_company))
```

The function receives the signed-in principal and the `AdminModel`
itself, and returns a SQLAlchemy condition. "Every row" is said
explicitly, with `sqlalchemy.true()`. It may be `async` (to look the
tenant up in another table, for instance). The condition is ANDed into
**every** route that reads or writes a row of the model:

- list, count (in the header and on the dashboard card) and export;
- detail, the edit form and its `POST`, delete and the upload download;
- bulk actions — delete, activate/deactivate — and custom actions: the
  selected ids outside the scope are dropped as if they did not exist;
- autocomplete, foreign-key `<select>` and foreign-key filter **when this
  model is the target**, and the inline table when it is the **child**;
- the editable inline formset (the parent and each child are read in
  scope);
- the CSV and `.xlsx` import preview and confirm, and the `.xlsx` export;
- duplicate (the source is read in scope), bulk edit, the `m2m_fields`
  options and links and the `related_counts` count (with the target's or
  child's scope), and the recent-activity feed.

A row outside the scope answers `404` — the same as a row that does not
exist, so the response never confirms it is there. On delete and the
built-in bulk actions, the condition is inside the `DELETE` / `UPDATE`
statement itself, not only in the read before it.

!!! danger "Returning `None` does not open everything"
    A scope function that forgets its `return` hands back `None`. The panel
    treats that as an error: it raises `AdminRowScopeError`, logs the model
    name and the request answers `500` — it fails closed instead of opening
    every row. To let everything through, return `true()`.

    And mind the principal without a tenant: `TicketModel.company_id ==
    user.company_id` with `company_id` `None` becomes `IS NULL`, which
    matches every row without a company. If that user should see nothing,
    return `false()` first, as in the example.

### On writes

The rule holds for the result: a row that is **created**, **edited** or
**imported** must still match the scope after the write. The panel writes
inside a transaction, counts the row against the condition in that same
transaction and, if it left, rolls back and returns the form with `This
record would fall outside the rows you may manage; nothing was saved.` —
company A's operator cannot create a company B ticket, nor "move" one of
theirs there by changing `company_id`. The same applies to:

- **`create_handler`**: the schema fields named like a foreign-key column
  go through the foreign-key check below before the handler runs. Then
  every row of the model the handler writes through the session —
  inserted **or changed**, as in an upsert that edits an existing row — is
  counted against the scope before the commit; outside it, rollback.
  Escaping the check: writes through SQL Core (`insert()`, `update()`,
  `bulk_create_values`), a service that opens a session of its own, and
  rows of **other** models the handler writes. A handler that commits on
  its own has already made the row durable: the panel cannot undo it,
  logs it at `ERROR` with the model, and says on the form that the record
  **was** saved outside the scope, instead of claiming nothing was saved.
- **bulk activate/deactivate**: if the new `is_active` takes any row out
  of the scope (a scope that filters on it), nothing changes and the list
  comes back with the warning.
- **bulk edit** (`bulk_edit_fields=`): the same rule — the `UPDATE` carries
  the scope in its `WHERE`, and a value that takes any row out of the scope
  rolls the whole transaction back.
- **many-to-many** (`m2m_fields=`): every posted id must be within the
  target's scope, and a link the operator does not reach is kept.
- **foreign keys**: on create, edit, inline or import, the value of a
  foreign key whose target model has a `row_scope` must be a row the
  principal reaches there. Outside the scope the field gets `Select a
  valid choice; that record does not exist.` — the same error as an id
  that does not exist, so a forged `POST` learns no other tenant's ids.
  The column is checked against its first `ForeignKey`: in a composite
  `ForeignKeyConstraint`, or on a column with several foreign keys, only
  that target is verified.

An uploaded file stored by a create or edit that ends up refused is
deleted (one attempt; a failure goes to the log).

On the edit form, a foreign key pointing at a row the operator does
**not** reach (or past `FK_OPTION_CAP`) stays selected: the `<select>`
gains one option with the current value, labelled with the id alone, and
saving without touching it keeps the reference — before, the browser
posted the first option and the save rewrote the reference silently.

In the detail's audit panel, the author of a change is shown by name when
the user model is not registered on the site, or has no `row_scope`. With
a `row_scope`, the name shows only if the principal has `VIEW` on that
model and reaches that user through the scope; otherwise only the id.

On delete, when the `DELETE ... AND <scope>` removes nothing (the row
left the scope after it was read), the whole block is rolled back —
whatever a `PRE_DELETE` handler wrote in the session is undone —, the
answer is `404` and no `POST_DELETE` fires. `POST_DELETE` receives the
instance outside the session, as with `BaseRepository.delete`, so it
reads the columns even with `expire_on_commit=True`.

!!! warning "What the scope does not cover"
    - `row_scope` applies to the `/m/{slug}/...` routes and to what they
      show of another model. What runs your code or free SQL stays out:
      the dashboard cards (`dashboard_cards=`), indicator areas, maps
      (`location_maps=`) and the SQL console.
    - In a custom action, `ctx.ids` are already filtered, but
      `ctx.repository` is the model's unfiltered repository and the panel
      does not check the outcome: the action can read beyond the ids and
      can **move** a row out of the scope. That is its own business.
    - The storage browser (`/storage`, the `STORAGE` surface) serves any
      key in the bucket. If the scoped model's uploads live in the same
      bucket, whoever has the surface bypasses the upload download's scope
      — deny `STORAGE` to those roles in `surface_policy`.
    - Create and import remain a `UNIQUE` oracle across tenants: a code
      that exists in company B conflicts for company A. The way out is a
      composite `UNIQUE` constraint with the tenant column
      (`UniqueConstraint("company_id", "code")`).

!!! tip "Together with `access_policy`"
    The two compose: `access_policy` decides **whether** the principal
    opens the model and which action it takes; `row_scope` decides
    **which rows**. A support role with `VIEW` and a per-company scope sees
    only its own company's list and gets `404` on any other's detail.

## Log in as a user (`impersonate=`)

Support needs to see the screen the customer sees. The admin does not know
how your service signs users in, so the job is split: you hand over a
function that turns `(operator, target)` into the URL that opens the app as
the target, and the panel owns the rest — the permission, the
confirmation, the refusal of a target that is itself an admin, the record
of who entered as whom, and the redirect.

For a service whose login issues `JWTUtils` access tokens, the handler is
ready-made:

```python
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    JWTUtils,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()
tokens = JWTUtils(settings.JWT_SECRET)

site.register(
    AdminModel(
        model=UserModel,
        audit_model=AuditLog,
        impersonate=make_jwt_impersonation_handler(
            tokens,
            redirect_url="https://app.example.com/impersonate#token={token}",
            ttl=timedelta(minutes=10),
        ),
    )
)


def policy(
    user: UserModel, admin: AdminModel[Any], action: AdminPermissionEnum
) -> bool:
    if action is AdminPermissionEnum.IMPERSONATE:
        return user.role == "support-lead"
    return True


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        access_policy=policy,
    ),
)
```

The signed token carries `sub` (the account's `id`), `typ` = `"access"` — so
`make_bearer_token_dependency` and `make_jwt_user_dependency` on the same
secret accept it like any access token —, the target's `email` when it has
one, and `act = {"sub": "<operator id>"}`, the RFC 8693 actor claim: the
app knows the session is an impersonation and who is behind it. The
default lifetime is `ADMIN_IMPERSONATION_TTL` (15 minutes). `redirect_url`
must contain `{token}` (otherwise `ValueError` at construction); prefer
putting the token in the fragment (`#`), which the browser sends neither to
the app's server nor in the `Referer`.

Apart from `act`, it is an ordinary access token: **every** route that
accepts the user's own token accepts this one. For a route that must not run
under an impersonated session — changing the password, moving money —,
`token_actor(claims)` returns the operator behind it (`None` on the user's
own session; a malformed `act` comes back as `"unknown"`, never as `None`):

```python
from typing import Any

from fastapi import Depends

from tempest_fastapi_sdk import ForbiddenException, token_actor

from src.api.dependencies.auth import bearer


async def own_session_only(
    claims: dict[str, Any] = Depends(bearer),
) -> dict[str, Any]:
    if token_actor(claims) is not None:
        raise ForbiddenException(message="Unavailable while impersonating.")
    return claims
```

When `impersonate=` sits on a model that is not the account — a profile, a
membership —, say which account it stands for with `impersonate_account=`
(sync or `async`). The handler receives that account, and the admin refusal
runs on it. The account must have an `id` (that is what goes into the
credential) and, when its class has a registered admin with a `row_scope`,
be inside that scope for the operator — otherwise a membership of company A
would hand out a user of company B. `None`, an account without `id` or one
outside the scope answer `404`, before any audit. A sync resolver runs
inside `AsyncSession.run_sync`, so it may read a lazy relationship
(`membership.user`); an `async` one must load what it reads (`selectinload`,
`AsyncAttrs.awaitable_attrs`):

```python
from tempest_fastapi_sdk import AdminModel, JWTUtils
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.core.settings import settings
from src.db.models import MembershipModel

site.register(
    AdminModel(
        model=MembershipModel,
        impersonate=make_jwt_impersonation_handler(
            JWTUtils(settings.JWT_SECRET),
            redirect_url="https://app.example.com/impersonate#token={token}",
        ),
        impersonate_account=lambda membership: membership.user,
    )
)
```

Another sign-in mechanism (magic link, cookie session) is just another
function of the same shape — `handler(operator, account) -> str`, sync or
`async`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import UserModel
from src.services.magic_links import issue_magic_link


async def enter_as(operator: UserModel, target: UserModel) -> str:
    link = await issue_magic_link(target.id, actor_id=operator.id)
    return f"https://app.example.com/magic#link={link}"


site.register(AdminModel(model=UserModel, impersonate=enter_as))
```

As with the JWT, the credential goes in the fragment: in the path it would
land in the app server's access log.

!!! danger "The handler enters as the account it received"
    The admin-target refusal runs on the account the handler receives. A
    handler that enters as **something else** — `target.owner`, an id read
    from another table — goes around it and must refuse admin accounts on
    its own. Put `impersonate=` on the account's model, or use
    `impersonate_account=`.

The flow in the panel:

1. The row's detail gains a **Log in as** button.
2. It opens a confirmation page; confirming `POST`s the CSRF token and a
   single-use nonce — reloading the answer does not enter again, it goes
   back to the list with the repeated-submission notice.
3. With `audit_model`, the audit entry is written (committed) **before**
   the handler runs; if the write fails, the handler does not run — no
   credential is minted — and the request ends in an error, with no `303`.
4. The handler runs and the browser gets a `303` to the returned URL, with
   `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. An
   `AppException` from the handler comes back to the confirmation with its
   message and is logged at `WARNING`. When the handler fails (any
   exception), a second entry, `admin.impersonate.failed` (reason in
   `context["reason"]`), says nothing was handed over; the timeline shows it
   as **log in as failed**.

!!! danger "`IMPERSONATE` never comes for free"
    Without an `access_policy` the admin allows everything — **except**
    impersonating: the button does not show and the route answers `403`.
    Acting as another account reaches past the admin's screens (and past
    its `row_scope` and audit), so only a policy that says yes to
    `AdminPermissionEnum.IMPERSONATE` grants it. Beware of a policy written
    as "everything but `DELETE`": it grants `IMPERSONATE` too. `EDIT` is not
    enough.

More rules on the way:

- **Scope.** The target must be inside the model's `row_scope`; outside it
  is `404`, as on every row route.
- **An admin is not a target.** An account that signs into the admin
  itself is refused (`403`, no form). Three questions go to the backend,
  any one refuses: `is_admin_account` on the row and on the resolved
  account (by default a truthy `is_admin`; on `UserModelAuthBackend`, also
  whatever your `is_authorized` answers — a backend that authorizes by role
  refuses that role), and `is_admin_identity(session, account_id)` on the
  `id` that goes into the credential. The last one catches a 1:1 profile
  sharing its primary key with an admin user: it asks `load_principal` and,
  when that finds nothing, `load_account` — which on `UserModelAuthBackend`
  reads the user row even when inactive or without MFA. On your own backend
  whose `load_principal` filters (inactive, MFA), **override
  `load_account`**: the default returns `None`, and an admin that
  `load_principal` drops would pass.
- **Record.** Every impersonation logs at `INFO` the operator, the model
  and the target's id — never the URL, which carries the credential. With
  `audit_model`, it also writes an `event` entry on the target, authored
  by the operator, **before** the handler (see step 3). With `AuditRequestMixin` on the table, the event
  (`admin.impersonate`), the IP and the user agent go to their own columns;
  without it, the name stays in `context["event"]`. The detail's timeline
  shows the entry as **logged in as**.
