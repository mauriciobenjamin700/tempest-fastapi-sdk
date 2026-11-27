# 6. Soft-delete e auditoria (mixins)

Use para adicionar soft-delete, `created_by`/`updated_by` e o idioma preferido do usuário com mixins.

Parte da receita [Banco de dados](../database.md).

Os mixins são **opt-in**: você os mistura ao lado de `BaseModel` só quando
o domínio pede. `SoftDeleteMixin` adiciona `deleted_at` (+
`mark_deleted()` / `mark_restored()` / `is_deleted`). `AuditMixin`
adiciona `created_by` / `updated_by` (+ `stamp_created_by` /
`stamp_updated_by`).

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

A filtragem é responsabilidade de quem chama — o mixin **não** instala um
filtro global. Esconda linhas soft-deleted passando `deleted_at=None`, ou
filtrando na subclasse. Carimbar auditoria pertence ao service, onde o
usuário atual está em escopo:

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

!!! tip "Dois carimbos de delete, propósitos diferentes"
    Use `repository.soft_delete(id)` (flag `is_active`) quando o booleano
    já basta. Use os helpers do `SoftDeleteMixin` (`mark_deleted` →
    `deleted_at`) quando precisa **saber quando** o delete aconteceu —
    auditoria, políticas de retenção.

!!! info "MFA é outro mixin opt-in"
    `MFAMixin` adiciona `totp_secret` / `totp_enabled_at` / `totp_last_step` ao modelo de
    usuário quando o projeto liga o fluxo MFA bundled. Detalhes em
    [MFA (TOTP / 2FA) »](../mfa.md).

## Locale — idioma preferido do usuário

`LocaleColumnMixin` adiciona uma coluna `locale` (BCP-47, ex. `"pt-BR"`,
`"en-US"`, nullable) pra o modelo carregar o idioma em que as notificações
e textos localizados dele devem sair — sem cada projeto redeclarar a mesma
coluna. Misture-o como qualquer outro mixin:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, LocaleColumnMixin


class UserModel(BaseModel, LocaleColumnMixin):
    """Users — carregam o locale de notificação."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
```

Pra escrever o valor, use o enum `LocaleEnum` (lista curada de tags BCP-47) em
vez de digitar a string na mão — cada membro **é** a própria tag, então
compara e grava como ela:

```python
from tempest_fastapi_sdk import LocaleEnum

from src.db.models import UserModel

user = UserModel(name="Ana", email="ana@example.com")


user.locale = LocaleEnum.PT_BR          # grava "pt-BR"
user.locale = "en-US"               # a string crua também vale
assert LocaleEnum.PT_BR == "pt-BR"      # membro é str
```

`locale` `NULL` significa "sem preferência": resolva pro default da sua app
na hora de renderizar (tipicamente via
[`MessageCatalog`](../../reference.md)), **não** trate como erro. Esse é
exatamente o par que o [recipe de Web Push »](../webpush.md) usa pra localizar
o `title`/`body` de cada notificação pelo `locale` do destinatário.

!!! note "`LocaleEnum` é curado, não exaustivo"
    O enum cobre os locales mais usados (pt/en/es/fr/de/… + variantes de
    região). Precisa de uma tag fora da lista? A coluna é `str`, então
    guarde a string crua e proponha o novo membro upstream quando virar
    comum.

**Recap:** mixins entram só quando o domínio precisa; a filtragem de
soft-delete é sua (`deleted_at IS NULL` via query crua); o carimbo de
auditoria mora no service; o `locale` do usuário vem do `LocaleColumnMixin`
+ enum `LocaleEnum`.
