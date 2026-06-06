# Painel admin


UI de gerenciamento no estilo Django montada sob `/admin`. Operadores entram com uma linha de usuário do banco (sem store de senha de admin separado) e navegam por todo modelo registrado pelo navegador, então a porta do banco pode ficar fechada em redes privadas. A Fase 1 entrega views somente leitura; criar/editar/apagar chegam na 0.14.0 e ações inline + em lote na 0.15.0.

Requer o extra `[admin]`:

```bash
pip install "tempest-fastapi-sdk[admin]"
```

#### 1. Modelo de usuário

Subclasse `BaseUserModel` para ganhar as quatro colunas que o backend de auth do admin espera (`email`, `hashed_password`, `is_admin`, `last_login_at`) em cima da linha padrão do `BaseModel`:

```python
# src/db/models/user.py
from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    __tablename__ = "users"   # scaffold convention; admin slug derives from __tablename__
```

`set_password()` / `check_password()` delegam ao `PasswordUtils`; `normalize_email()` deixa minúsculo e remove espaços. O `is_active` padrão (herdado do `BaseModel`) e o `is_admin` (default `False`) controlam o acesso — somente linhas `is_active=True` E `is_admin=True` podem entrar.

Faça o bootstrap do primeiro admin pela sua CLI / migração / script de seed. O script completo conecta um `AsyncDatabaseManager`, abre uma sessão, insere a linha e dá commit — exatamente o mesmo padrão que seus repositories seguem em runtime:

```python
# scripts/create_admin.py
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager

from src.core.settings import settings
from src.db.models import UserModel


async def main() -> None:
    db = AsyncDatabaseManager(settings.DATABASE_URL)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            # ──────── the only admin-specific lines ────────
            admin = UserModel(email="root@example.com", is_admin=True)
            admin.set_password("hunter2")  # bcrypt via PasswordUtils
            session.add(admin)
            await session.commit()
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
```

As quatro linhas destacadas sob o comentário divisor são o único código de bootstrap específico de admin; tudo ao redor é o ciclo de vida async de DB padrão que o SDK já usa.

#### 2. Registre suas classes de admin

`AdminModel` é uma instância de configuração tipada simples — a assinatura do construtor é o contrato (sem mágica de atributo de classe / metaclass), e todo campo aceita um atributo de coluna SQLAlchemy real (`UserModel.email`), então erros de digitação aparecem no seu editor em vez de em runtime. Os defaults funcionam de cara; passe os campos que quiser para enriquecer a list view:

```python
# src/admin/site.py
from sqlalchemy import desc

from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import UserModel, OrderModel

site = AdminSite(
    title="MyApp Admin",
    index_subtitle="Site administration",
    site_url="https://myapp.com",   # optional outbound "View site" link
)

site.register(AdminModel(
    model=UserModel,
    list_display=[UserModel.email, UserModel.is_admin, UserModel.is_active, UserModel.last_login_at],
    list_filter=[UserModel.is_active, UserModel.is_admin],
    search_fields=[UserModel.email],
    readonly_fields=[UserModel.id, UserModel.hashed_password, UserModel.created_at, UserModel.updated_at],
    ordering=desc(UserModel.created_at),
    page_size=25,
))
```

Toda referência a campo também aceita uma string simples (`list_display=["email", ...]`) para configuração dinâmica, e `ordering` aceita uma coluna (ascendente), `desc(column)` / `asc(column)`, ou uma string no estilo Django `"-created_at"`. `register` retorna a instância e levanta `ValueError` em slug duplicado. Os slugs derivam por padrão do `__tablename__` do modelo, para que URLs e tabelas do banco fiquem em sincronia.

#### 3. Monte o router

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin.site import site
from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
app = FastAPI()
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,          # scaffold reuses JWT_SECRET — pelo menos 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
    )
)
```

`make_admin_router` monta:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — fluxo de auth.
- `GET  /admin/` — dashboard listando todo admin registrado.
- `GET  /admin/m/{slug}/` — list view com paginação + busca em texto livre (`?q=`) + filtros por campo (`?filter_<field>=value`) + **ordenação por coluna** clicável (`?sort=<coluna>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **exporta** o resultado atual (respeitando busca/filtros/ordenação) como CSV ou JSON. Limite de linhas via `make_admin_router(export_max_rows=…)` (default 5000).
- `POST /admin/m/{slug}/bulk` — **ações em massa** (delete / activate / deactivate) nas linhas selecionadas.
- `GET/POST /admin/m/{slug}/new` — **criar** registro (quando `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view com botões Edit/Delete.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **editar** registro (quando `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **excluir** registro (quando `can_delete`).
- `GET  /admin/static/{path}` — assets CSS/HTMX embutidos.

!!! info "Escrita (CRUD) + permissões"
    Create/edit/delete são controlados por flags no `AdminModel`: `can_create` / `can_edit` / `can_delete` (todas `True` por default; uma view desativada responde `404`). Todo POST de escrita carrega o token CSRF da sessão, validado no servidor (`403` em mismatch). Os **widgets de campo** são derivados do tipo da coluna — texto / textarea (strings longas) / number / checkbox / `datetime-local` / date / `select` para enums — com validação de obrigatórios + erros por campo re-renderizados no formulário.

    **Ações em massa**: a list view mostra checkboxes por linha + select-all e uma barra de ação (delete / activate / deactivate) que opera nas linhas marcadas via `POST .../bulk` (CSRF + flags `can_delete`/`can_edit`), apoiada em `BaseRepository.delete_batch` / `bulk_update`.

    Ainda **não** incluídos (fases futuras do roadmap): widget FK-select, upload de arquivo, inline/related editing.

!!! tip "Responsivo por padrão"
    Os templates + CSS embutidos são responsivos: em telas estreitas (≤600px) o header empilha, busca/filtros/ações viram full-width, as tabelas ganham scroll horizontal (nunca quebram o layout) e o grid do detail colapsa para uma coluna. Headers de coluna são clicáveis para alternar a ordenação (▲/▼).

#### 4. Defaults de segurança de sessão

`SignedCookieSessionStore` usa `itsdangerous.TimestampSigner` (HMAC-SHA256) para assinar um único cookie:

- `HttpOnly` sempre definido.
- `Secure` marcado quando `cookie_secure=True` (padrão; desligue no dev HTTP local).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` aceitos).
- Tempo de vida padrão `8h`; cookies expirados ou adulterados são rejeitados silenciosamente.
- Um token CSRF por sessão é gerado no login e exigido por todo POST de formulário (apenas `logout` na Fase 1).
- `secret_key` deve ter ao menos 32 bytes — chaves curtas levantam `ValueError` no momento da construção.

#### 5. Plugue um backend de auth customizado

`AdminAuthBackend` é uma ABC, então troque o default por LDAP / OAuth / IAM externo subclasseando:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError


class OAuthAdminBackend(AdminAuthBackend):
    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> Any:
        principal = await my_oauth_client.authenticate(identifier, password)
        if not principal.has_role("admin"):
            raise AdminAuthError("not an admin")
        return principal

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> Any | None:
        return await my_oauth_client.get_user(principal_id)

    def principal_id(self, principal: Any) -> str:
        return principal.sub

    def display_name(self, principal: Any) -> str:
        return principal.email
```

Passe a instância via `auth_backend=` e o resto do pipeline do admin (sessões, dashboard, list, detail) segue funcionando sem mudanças.
