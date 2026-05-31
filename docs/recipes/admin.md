# Site de administração {#admin-site}


UI de gerenciamento no estilo Django montada sob `/admin`. Os operadores fazem login com uma linha de usuário do banco de dados (sem um armazenamento de senha de admin separado) e navegam por todos os modelos registrados a partir do navegador, de modo que a porta do banco de dados pode permanecer fechada em redes privadas. A fase 1 entrega visualizações somente leitura; criar/editar/excluir chegam na 0.14.0 e as ações inline + em lote na 0.15.0.

Requer o extra `[admin]`:

```bash
pip install "tempest-fastapi-sdk[admin]"
```

#### 1. Modelo de usuário {#1-user-model}

Faça uma subclasse de `BaseUserModel` para obter as quatro colunas que o backend de autenticação do admin espera (`email`, `hashed_password`, `is_admin`, `last_login_at`) por cima da linha padrão do `BaseModel`:

```python
# src/db/models/user.py
from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    __tablename__ = "user"
```

`set_password()` / `check_password()` delegam para `PasswordUtils`; `normalize_email()` converte para minúsculas e remove espaços. O `is_active` padrão (herdado de `BaseModel`) e o `is_admin` (com padrão `False`) controlam o acesso — apenas linhas com `is_active=True` E `is_admin=True` podem fazer login.

Crie o primeiro admin via seu CLI / migration / script de seed. O script completo conecta um `AsyncDatabaseManager`, abre uma sessão, insere a linha e faz commit — exatamente o mesmo padrão que seus repositórios seguem em tempo de execução:

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

As quatro linhas destacadas sob o comentário divisor são o único código de bootstrap do admin; tudo ao redor é o ciclo de vida assíncrono padrão do banco de dados que o SDK já usa.

#### 2. Registre suas classes de admin {#2-register-your-admin-classes}

`AdminModel` é uma instância de configuração tipada e simples — a assinatura do construtor é o contrato (sem mágica de atributo de classe / metaclasse), e cada campo aceita um atributo de coluna real do SQLAlchemy (`UserModel.email`), de modo que erros de digitação aparecem no seu editor em vez de em tempo de execução. Os padrões funcionam de imediato; passe os campos que quiser para enriquecer a visualização de lista:

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

Cada referência de campo também aceita uma string simples (`list_display=["email", ...]`) para configuração dinâmica, e `ordering` aceita uma coluna (ascendente), `desc(column)` / `asc(column)`, ou uma string no estilo Django `"-created_at"`. `register` retorna a instância e levanta `ValueError` em caso de slug duplicado. Os slugs têm como padrão o `__tablename__` do modelo, então as URLs e as tabelas do banco de dados ficam em sincronia.

#### 3. Monte o router {#3-mount-the-router}

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
        secret_key=settings.ADMIN_SECRET_KEY,    # at least 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
    )
)
```

`make_admin_router` monta:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — fluxo de autenticação.
- `GET  /admin/` — dashboard listando cada admin registrado.
- `GET  /admin/m/{slug}/` — visualização de lista com paginação + busca em texto livre (`?q=`) + filtros por campo (`?filter_<field>=value`).
- `GET  /admin/m/{slug}/{identity}` — visualização de detalhe somente leitura.
- `GET  /admin/static/{path}` — assets de CSS/HTMX empacotados.

#### 4. Padrões de segurança de sessão {#4-session-security-defaults}

`SignedCookieSessionStore` usa `itsdangerous.TimestampSigner` (HMAC-SHA256) para assinar um único cookie:

- `HttpOnly` sempre definido.
- `Secure` sinalizado quando `cookie_secure=True` (padrão; desligue no dev local em HTTP).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` aceitos).
- Tempo de vida padrão `8h`; cookies expirados ou adulterados são rejeitados silenciosamente.
- Um token CSRF por sessão é gerado no login e exigido em todo POST de formulário (apenas `logout` na fase 1).
- `secret_key` deve ter ao menos 32 bytes — chaves curtas levantam `ValueError` no momento da construção.

#### 5. Conecte um backend de autenticação personalizado {#5-plug-in-a-custom-auth-backend}

`AdminAuthBackend` é uma ABC, então troque o padrão por LDAP / OAuth / IAM externo fazendo uma subclasse:

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

Passe a instância via `auth_backend=` e o resto do pipeline do admin (sessões, dashboard, lista, detalhe) continua funcionando sem alterações.
