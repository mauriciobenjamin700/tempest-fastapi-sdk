---
description: "Use to add soft-delete (`SoftDeleteMixin`), `created_by`/`updated_by` and the user's preferred language to a model with opt-in mixins."
---

# 6. Soft-delete and auditing (mixins)

Use to add soft-delete, `created_by`/`updated_by` and the user's preferred language through mixins.

Part of the [Database](../database.md) recipe.

The mixins are **opt-in**: you mix them alongside `BaseModel` only when
the domain asks. `SoftDeleteMixin` adds `deleted_at` (+ `mark_deleted()` /
`mark_restored()` / `is_deleted`). `AuditMixin` adds `created_by` /
`updated_by` (+ `stamp_created_by` / `stamp_updated_by`).

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AuditMixin, BaseModel, SoftDeleteMixin


class UserModel(BaseModel, SoftDeleteMixin, AuditMixin):
    """Users — soft-deletable and audited."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

Filtering is the caller's responsibility — the mixin does **not** install
a global filter. Hide soft-deleted rows by passing `deleted_at=None`, or
filter in the subclass. Stamping audit columns belongs in the service,
where the current user is in scope:

```python
# src/services/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from src.db.repositories import UserRepository
from src.schemas import UserResponse, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user domain."""

    async def list_alive(self) -> list[UserResponse]:
        """Return only rows where ``deleted_at IS NULL``.

        Returns:
            list[UserResponse]: The alive users.
        """
        instances = await self.repository.list(filters={"deleted_at": None})
        return [self.repository.map_to_response(i) for i in instances]

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
        *,
        actor_id: UUID,
    ) -> UserResponse:
        """Apply a partial update and stamp ``updated_by`` with the actor.

        Args:
            user_id (UUID): Primary key of the row to update.
            data (UserUpdateSchema): The partial payload.
            actor_id (UUID): The acting user, written to ``updated_by``.

        Returns:
            UserResponse: The updated user.
        """
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(data.model_dump(exclude_unset=True))
        instance.stamp_updated_by(actor_id)
        updated = await self.repository.update(instance)
        return self.repository.map_to_response(updated)
```

!!! tip "Two delete stamps, different purposes"
    Use `repository.soft_delete(id)` (the `is_active` flag) when the
    boolean is enough. Use the `SoftDeleteMixin` helpers (`mark_deleted` →
    `deleted_at`) when you need to know **when** the delete happened —
    auditing, retention policies.

!!! info "MFA is another opt-in mixin"
    `MFAMixin` adds `totp_secret` / `totp_enabled_at` / `totp_last_step` to the user model
    when the project turns on the bundled MFA flow. Details in
    [MFA (TOTP / 2FA) »](../mfa.md).

## Locale — the user's preferred language

`LocaleColumnMixin` adds a `locale` column (BCP-47, e.g. `"pt-BR"`,
`"en-US"`, nullable) so a model can carry the language its notifications and
localized text should render in — without every project re-declaring the
same column. Mix it in like any other mixin:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, LocaleColumnMixin


class UserModel(BaseModel, LocaleColumnMixin):
    """Users — carry a notification locale."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
```

To write the value, use the `LocaleEnum` enum (a curated list of BCP-47 tags)
instead of typing the string by hand — each member **is** the tag itself, so
it compares and stores as that tag:

```python
from tempest_fastapi_sdk import LocaleEnum

from src.db.models import UserModel

user = UserModel(name="Ana", email="ana@example.com")


user.locale = LocaleEnum.PT_BR          # stores "pt-BR"
user.locale = "en-US"               # a raw string works too
assert LocaleEnum.PT_BR == "pt-BR"      # a member is a str
```

A `NULL` `locale` means "no preference": resolve it to your app's default
when rendering (typically via [`MessageCatalog`](../../reference.md)), **not**
as an error. This is exactly the pair the [Web Push recipe »](../webpush.md)
uses to localize each notification's `title`/`body` by the recipient's
`locale`.

!!! note "`LocaleEnum` is curated, not exhaustive"
    The enum covers the most widely used locales (pt/en/es/fr/de/… plus
    regional variants). Need a tag outside the list? The column is a `str`,
    so store the raw string and propose the new member upstream once it
    becomes common.

**Recap:** mixins enter only when the domain needs them; soft-delete
filtering is yours (`deleted_at IS NULL` via a raw query); the audit stamp
lives in the service; the user's `locale` comes from `LocaleColumnMixin` +
the `LocaleEnum` enum.
