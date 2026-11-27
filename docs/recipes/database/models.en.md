# 1. The base model

Use to declare tables with `BaseModel`: centralized table names, the constraint naming convention and the helpers you get for free.

Part of the [Database](../database.md) recipe.

Every model in your service inherits from `BaseModel`. You get four
columns without writing any:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """Users table."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

That already creates the `user` table with **seven** columns: your three
(`name`, `email`, `password_hash`) plus the four inherited ones:

| Column | Type | Default | Role |
| --- | --- | --- | --- |
| `id` | `UUID` (v4) | `uuid4()` | Primary key, portable across Postgres/SQLite/MySQL/MSSQL. |
| `is_active` | `bool` | `True` | Fast soft-delete flag. |
| `created_at` | `datetime` (tz-aware) | `utcnow()` on flush | Creation timestamp. |
| `updated_at` | `datetime` (tz-aware) | `utcnow()` on `onupdate` | Last-write timestamp. |

!!! info "Why is the table named `user` and not `UserModel`?"
    `BaseModel` derives `__tablename__` from the class automatically: it
    strips the `Model` suffix and converts to `snake_case`. `UserModel` →
    `user`, `OrderItemModel` → `order_item`. You can always pin
    `__tablename__ = "users"` explicitly — the explicit declaration wins
    over the automatic one.

!!! tip "Pinning the name is not just taste"
    `USER` is a reserved word in standard SQL. SQLAlchemy always quotes
    the identifier, so your application works — but a `SELECT * FROM
    user` typed by hand in `psql` returns the **database user**, not your
    table, and raises nothing. The plural (`users`) sidesteps that, and
    it is the convention the SDK itself assumes for the token tables
    (`user_tokens`, `user_refresh_tokens`).

## Centralizing table names

A table name almost never appears just once. It is in `__tablename__`
**and** it comes back as a string in every `ForeignKey` pointing at it:

```python hl_lines="9 12"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserTokenModel(BaseModel):
    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
```

Renaming `users` now depends on you remembering every place the string
shows up. And missing a FK does not blow up right away: SQLAlchemy only
resolves the target when it configures the mappers, so the error lands at
**application startup** — or, worse, in a migration pointing at a table
that no longer exists.

The fix is a module that holds nothing but names:

```python
# src/db/configs/names.py
"""Table names for this project. Single source of truth."""

USER_TABLE_NAME = "users"
USER_TOKEN_TABLE_NAME = "user_tokens"
USER_REFRESH_TOKEN_TABLE_NAME = "user_refresh_tokens"
ORDER_TABLE_NAME = "orders"
ORDER_ITEM_TABLE_NAME = "order_items"
```

The `_TABLE_NAME` suffix keeps the constant self-explanatory at the call
site, far from this file: `ForeignKey(f"{USER_TABLE_NAME}.id")` says what
that string is on its own. The prefix follows the model, in the singular
(`UserTokenModel` → `USER_TOKEN_TABLE_NAME`), even when the value is
plural.

Every model imports from there, on both sides of the relationship:

```python hl_lines="6 10"
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    __tablename__ = USER_TABLE_NAME

    email: Mapped[str] = mapped_column(unique=True)
```

```python hl_lines="9 13 16"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME, USER_TOKEN_TABLE_NAME


class UserTokenModel(BaseModel):
    __tablename__ = USER_TOKEN_TABLE_NAME

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{USER_TABLE_NAME}.id", ondelete="CASCADE"),
        index=True,
    )
```

The payoff shows up when you change something. Renaming a table becomes
**one line** in `names.py`. Your editor's "find usages" locates everyone
who depends on it, because it is now a symbol instead of a loose string.
And no `ForeignKey` can point at a table that no longer exists without
the import breaking first.

!!! tip "Why `db/configs/` and not `core/constants.py`?"
    A table name is a database detail, and `db/models/` is what consumes
    it. Keeping it under `db/` keeps the dependency inside its own layer,
    and the module ends up importing nothing from the project — it is
    only strings. That is what guarantees it never joins an import cycle:
    `models` imports `configs`, and `configs` imports nobody.

!!! check "It applies to the SDK's tables too"
    The abstract models the SDK ships (`BaseUserModel`,
    `BaseUserTokenModel`, `BaseUserRefreshTokenModel`,
    `BaseWebPushSubscriptionModel`, `BaseOutboxModel`) deliberately leave
    `__tablename__` and the FK for the concrete project to declare —
    precisely so both can come out of your `names.py`, under your naming
    convention.

## Explicit `__tablename__` with Pyright

`BaseModel` declares `__tablename__` as a `@declared_attr.directive`,
SQLAlchemy 2.0's mechanism for deriving the name from the class. mypy
understands a subclass overriding that with a string and **does not
complain** — it is the checker `tempest type` runs, so the default gate
stays clean.

Pyright is stricter: it reads the inherited attribute as a mutable
variable with an invariant type and flags the assignment.

!!! warning "`reportIncompatibleVariableOverride` in Pyright/Pylance"
    ```text
    Type "Literal['users']" is not assignable to declared type
    "_declared_directive[str]" (reportAssignmentType)
    ```

    This is not a defect in your model: the assignment works at runtime,
    and it is the form used throughout these docs. It is Pyright being
    stricter than mypy about overriding a descriptor.

If your editor runs Pyright and you want the file clean, declare the name
through the same mechanism the base class uses:

```python hl_lines="7 8"
from sqlalchemy.orm import declared_attr
from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Pin the table name."""
        return USER_TABLE_NAME
```

More verbose, and equivalent at runtime. Pick by the project's checker:
with mypy (or no static checking in the editor), prefer
`__tablename__ = USER_TABLE_NAME`, which reads more directly.

## Constraint naming convention

`BaseModel.metadata` ships configured with `NAMING_CONVENTION`. That makes
every PK/FK/index/unique/check get a **deterministic** name —
`ix_user_email`, `uq_user_email`, `fk_order_user_id_user` — identical on
every machine and every engine.

!!! check "The real win is in the migrations"
    Without deterministic names, `alembic revision --autogenerate` invents
    random identifiers and each developer generates a different diff for
    the same schema. With the convention, autogenerate only emits **real
    schema diffs** — no name churn.

## Composite unique — the name carries every column

A uniqueness rule spanning more than one column ("the same title cannot repeat
in the same year") is a `UniqueConstraint` with several columns in
`__table_args__`. You do not need to name it — the convention does:

```python
# src/db/models/book.py
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class BookModel(BaseModel):
    """Catalog books."""

    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("isbn"),
        UniqueConstraint("title", "release_year"),
        UniqueConstraint("title", "author"),
        Index(None, "author", "title"),
    )

    isbn: Mapped[str] = mapped_column()
    title: Mapped[str] = mapped_column()
    release_year: Mapped[int] = mapped_column()
    author: Mapped[str] = mapped_column()


names: list[str] = sorted(
    str(item.name)
    for item in [*BookModel.__table__.constraints, *BookModel.__table__.indexes]
)
print(names)
```

Output:

```text
['ix_books_author_books_title', 'pk_books', 'uq_books_isbn', 'uq_books_title_author', 'uq_books_title_release_year']
```

Every composite constraint gets **all** its columns in the name, joined with
`_`: `uq_books_title_release_year` and `uq_books_title_author` are two
different rules with two different names. The templates use SQLAlchemy's
`column_0_N_*` tokens, which for a single column render exactly what the
`column_0_*` tokens rendered — so `uq_books_isbn`, `ix_user_email` and
`fk_order_user_id_user` stay the same.

A distinct name is what makes the `constraint` Postgres returns through
[`parse_integrity_error`](repository.md#which-constraint-refused-parse_integrity_error)
useful: a `name → error code` dict now points at exactly one rule.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error

CONFLICT_CODES: dict[str, str] = {
    "uq_books_isbn": "ISBN_TAKEN",
    "uq_books_title_release_year": "TITLE_TAKEN_THIS_YEAR",
    "uq_books_title_author": "AUTHOR_ALREADY_HAS_TITLE",
}


def to_conflict(error: IntegrityError) -> dict[str, Any]:
    """Translate the violation into the code the client branches on."""
    failure = parse_integrity_error(error)
    return {
        "code": CONFLICT_CODES.get(failure.constraint or "", "CONFLICT"),
        "fields": list(failure.columns),
    }
```

!!! warning "Before this fix, the two uniques shared one name"
    Up to the previous version, the `uq`, `ix` and `fk` templates read **only
    the first column**. `UniqueConstraint("title")` and
    `UniqueConstraint("title", "release_year")` both became
    `uq_books_title`. PostgreSQL refuses the `CREATE TABLE` with
    `relation "uq_books_title" already exists`; SQLite accepts both, which is
    why the defect slipped past the test database. A database created under
    the old convention still holds the old names for its composite
    constraints — the step-by-step rename is in
    [Migrate composite constraints from the old convention](migrations.md#migrate-composite-constraints-from-the-old-convention).

!!! note "Names past 63 characters"
    PostgreSQL accepts identifiers of up to 63 characters. When a convention
    name is longer, SQLAlchemy shortens it while emitting the DDL: the first
    55 characters, `_`, and the last 4 hex digits of the full name's MD5 —
    `uq_subscription_billing_events_customer_identifier_bill_f06e` for a
    90-character unique. It is the same on every run, and autogenerate
    reports no difference afterwards (measured against PostgreSQL 16).
    SQLite has no limit and keeps the full name.

??? info "The one ambiguity left"
    Joining with `_` leaves one case the convention cannot tell apart:
    columns `("a_b", "c")` and `("a", "b_c")` on the same table render the
    same name. If your schema has such a pair, give one of them an explicit
    `name=` — an explicit name wins over the convention.

## Helpers you get for free

Every `BaseModel` instance gets:

```python
# Serialize to a dict (handy in logs/tests)

from typing import Any

from src.db.models import UserModel
from src.schemas import UserUpdateSchema

payload = UserUpdateSchema(name="Ana Paula")
user = UserModel(name="Ana", email="ana@example.com")


data: dict[str, Any] = user.to_dict(exclude=["password_hash"])

# Assign many fields at once, with a whitelist against mass-assignment
user.update_from_dict(
    payload.model_dump(exclude_unset=True),
    allowed_fields={"name", "email"},   # id/role never get written
)
```

`__eq__` and `__hash__` compare by `(type, id)`, so the same row loaded
across different sessions compares equal — handy in tests and `set`s. Rows
not yet persisted (`id is None`) fall back to Python identity.

!!! warning "Always use `allowed_fields` on external payloads"
    `update_from_dict` without `allowed_fields` accepts any mapped column.
    For PATCH bodies coming from the client, pass the whitelist — it's the
    defense against mass-assignment on sensitive columns (`id`, `role`,
    `is_active`).

## The table name — `get_table_name()`

A dict key, a panel lookup, a test assertion: when you need the table name,
ask the model, not the dunder:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite, BaseModel


class OrderItemModel(BaseModel):
    """An order line."""


print(OrderItemModel.get_table_name())  # order_item

site = AdminSite(title="Panel")
site.register(AdminModel(model=OrderItemModel))
admin = site.get(OrderItemModel)
```

`get_table_name()` reads the table the mapper built, not `__tablename__`:
it answers the name derived from the class, an explicit `__tablename__`, and
also a model declared with `__table__ = Table(...)`, which has no
`__tablename__` at all. Under single-table inheritance a subclass answers its
parent's table, where its rows live. The return type is `str` for the type
checker, and `AdminSite.get` / `require` / `unregister` take the class itself
in place of the string.

**Recap:** inherit `BaseModel`, declare only your domain columns, and the
SDK delivers id/timestamps/soft-delete, deterministic constraint names
(a composite unique carries every column in its name) and serialization
helpers.
