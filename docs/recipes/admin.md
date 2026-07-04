# Painel admin


UI de gerenciamento no estilo Django montada sob `/admin`. Operadores entram com uma linha de usuário do próprio banco — não há store de senha de admin separado. Cada modelo registrado passa a ser navegável pelo navegador, então a porta do banco pode ficar fechada em redes privadas.

**O que você ganha** (paridade com o Django admin):

- List view com busca, filtros ricos por campo (enum / FK / range de data) e colunas ordenáveis.
- CRUD completo (criar / editar / excluir) e ações em massa.
- Export CSV/JSON e widgets FK-select.
- Dashboard com contagens de linhas + métricas de sistema.
- MFA TOTP opcional no login.
- Campos de upload de arquivo/imagem.
- Trilha de auditoria carimbando `created_by` / `updated_by`.

Ainda no roadmap: edição inline/relacionada.

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
    brand="servus-backend-admin",     # texto centralizado no topo (opcional; default = title)
    index_subtitle="Site administration",
    site_url="https://myapp.com",     # optional outbound "View site" link
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

!!! info "Filtros automáticos por tipo de coluna"
    Cada campo em `list_filter` vira o widget certo conforme o tipo da
    coluna: **boolean** → dropdown Sim/Não; **enum** → dropdown com os
    membros; **FK** (cujo destino tem `AdminModel` registrado) → dropdown
    das linhas relacionadas (label pelo `search_fields`); **date/datetime**
    → dois inputs de data (de/até, range inclusivo); qualquer outra coluna
    → input de texto (igualdade). Tudo preserva busca/ordenação/paginação
    na URL.

!!! tip "Marca centralizada e customizável"
    O nome exibido no centro do header vem de `brand` (opcional). Sem ele, cai no `title` — então sites existentes não mudam. Use `brand` para mostrar um nome distinto (ex.: `"servus-backend-admin"`) centralizado no topo de toda página. A sidebar é fixa e **sobrepõe header e footer** no desktop (z-index maior) — comportamento automático do CSS embutido, sem config.

#### 2b. Atalho — registrar todos os modelos de uma vez (`automap`)

Em vez de um `register` por tabela, aponte `automap` para o pacote dos modelos e o SDK descobre e registra **todo `BaseModel` concreto** automaticamente. Bases abstratas (`BaseUserModel` e cia. — sem `__tablename__`) são puladas sozinhas:

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminModel, AdminSite

site = AdminSite(title="MyApp Admin", brand="servus-backend-admin")

# Carrega TODAS as tabelas de src/db/models de uma vez:
site.automap("src.db.models")
```

Misture os dois estilos: registre à mão os modelos que precisam de config própria, depois deixe o `automap` preencher o resto (ele pula slugs já registrados por padrão):

```python
# UserModel ganha config caprichada...
site.register(AdminModel(
    model=UserModel,
    list_display=[UserModel.email, UserModel.is_admin],
    search_fields=[UserModel.email],
))

# ...e o automap registra o resto com os defaults.
site.automap("src.db.models")
```

`automap` aceita: `exclude=[...]` (classe, nome de classe ou nome de tabela para esconder um modelo), `skip_registered=False` (levanta `ValueError` em colisão, igual `register`), e `**admin_kwargs` aplicados a todos (`page_size=50`, `can_delete=False`, ...). Para introspecção sem registrar, use a função `discover_models("src.db.models")` direto.

!!! warning "Config uniforme"
    Os `**admin_kwargs` do `automap` valem para **todos** os modelos descobertos. Quando um modelo precisa de `list_display` / `search_fields` próprios, registre-o à mão **antes** do `automap` (com `skip_registered=True`, o default).

#### 3. Monte o router

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin.site import site
from src.api.dependencies import db   # singleton de src/api/dependencies/resources.py
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,          # scaffold reuses JWT_SECRET — pelo menos 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
        show_logs=True,                          # liga a página de logs + item na sidebar
        log_dir=settings.LOG_DIR,                # mesmo dir passado pro configure_logging
    )
)
```

`make_admin_router` monta:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — fluxo de auth.
- `GET/POST /admin/mfa` — desafio TOTP (segundo fator) entre a senha e o acesso, para principais com MFA habilitado.
- `GET  /admin/` — dashboard: card por modelo com **contagem de linhas** + Browse/New, e um **painel de métricas** (CPU/RAM/disco via `MetricsUtils`). Painel ligado por default, omitido sem o extra `[metrics]`, desligável com `make_admin_router(show_metrics=False)`.
- `GET  /admin/logs` — **logs da aplicação** (quando `show_logs=True`): lê os arquivos JSON estruturados escritos pelo `configure_logging(log_dir=…)`, com filtro por fonte (`?source=`), busca em texto (`?q=`) e paginação. Badges coloridos por nível. Quando ainda não há arquivos de log, mostra um estado vazio.
- `GET  /admin/m/{slug}/` — list view com paginação + busca em texto livre (`?q=`) + filtros por campo (`?filter_<field>=value`) + **ordenação por coluna** clicável (`?sort=<coluna>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **exporta** o resultado atual (respeitando busca/filtros/ordenação) como CSV ou JSON. Limite de linhas via `make_admin_router(export_max_rows=…)` (default 5000).
- `POST /admin/m/{slug}/bulk` — **ações em massa** (delete / activate / deactivate + suas **ações customizadas**) nas linhas selecionadas.
- `GET/POST /admin/m/{slug}/new` — **criar** registro (quando `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view com botões Edit/Delete.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **editar** registro (quando `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **excluir** registro (quando `can_delete`).
- `GET  /admin/static/{path}` — assets CSS/HTMX embutidos.

!!! info "Escrita (CRUD) + permissões"
    Create/edit/delete são controlados por flags no `AdminModel`: `can_create` / `can_edit` / `can_delete` (todas `True` por default; uma view desativada responde `404`). Todo POST de escrita carrega o token CSRF da sessão, validado no servidor (`403` em mismatch). Os **widgets de campo** são derivados do tipo da coluna — texto / textarea (strings longas) / number / checkbox / `datetime-local` / date / `select` para enums — com validação de obrigatórios + erros por campo re-renderizados no formulário.

    **Ações em massa**: a list view mostra checkboxes por linha + select-all e uma barra de ação (delete / activate / deactivate) que opera nas linhas marcadas via `POST .../bulk` (CSRF + flags `can_delete`/`can_edit`), apoiada em `BaseRepository.delete_batch` / `bulk_update`.

    **FK-select**: uma coluna FK cujo destino tem `AdminModel` registrado vira um dropdown das linhas relacionadas (igual ao FK select do Django) no formulário, em vez de um input UUID cru. O label da opção vem do primeiro `search_fields` do admin referenciado (fallback: atributo `name`/`title`/`email`, depois o id). Limitado a 1000 linhas; FK para tabela não-gerenciada continua input UUID.

    **MFA no login**: um principal com MFA habilitado (colunas `totp_secret`/`totp_enabled_at` do `MFAMixin`) passa por um desafio TOTP em `/admin/mfa` depois da senha — só um código válido libera o acesso. Habilite passando um usuário com MFA via `UserModelAuthBackend(UserModel, mfa_issuer=...)`; backends customizados sobrescrevem `mfa_enabled`/`verify_mfa`.

    **Audit trail**: create/edit pelo admin carimba `created_by`/`updated_by` (do `AuditMixin`) com o id do admin atuante; o detail mostra um painel **Audit** com timestamps e — quando o modelo tem as colunas de auditoria — o ator (UUID resolvido para nome via o auth backend). Modelos sem `AuditMixin` mostram só os timestamps.

    Ainda **não** incluídos (fases futuras do roadmap): inline/related editing.

## Ações customizadas (`@admin_action`)

Além das 3 fixas (activate / deactivate / delete), você registra **ações
próprias** — uma função async decorada com `@admin_action` e passada em
`AdminModel(actions=[...])`. Cada uma vira uma opção no dropdown de ações
em massa, operando nas linhas marcadas.

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    admin_action,
)


@admin_action(label="Enviar boas-vindas")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Roda nas linhas selecionadas; a mensagem é exibida na list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send_welcome(user.email)
    return AdminActionResult(f"{len(users)} e-mails enviados.")


site.register(AdminModel(model=UserModel, actions=[send_welcome]))
```

O handler recebe um `AdminActionContext` com:

| Campo | O que é |
| --- | --- |
| `ids` | Identidades das linhas marcadas. |
| `repository` | `BaseRepository` do modelo, na sessão do request. |
| `db_session` | A sessão DB (pra trabalho além do repositório). |
| `request` | O request inbound. |
| `session` | A sessão do admin autenticado. |
| `principal` | A linha do usuário admin que disparou a ação. |

Retorne um `AdminActionResult(message, category="success"|"error"|"warning")`
pra exibir um banner na list view (ou `None` pra não mostrar nada). A
função fica **diretamente chamável/testável** — o decorator só anexa
metadados. Use `name=` pra fixar o identificador (default: nome da função)
e `dangerous=True` pra marcar ação destrutiva.

## Campo de upload de arquivo / imagem

Uma coluna `String` que guarda o caminho/chave de um arquivo pode virar um
**input de upload** no formulário. Liste a coluna em `upload_fields` e
passe um `upload_storage` (os backends que o SDK já tem —
`LocalUploadStorage` / `MinIOUploadStorage`). No submit, o arquivo é
salvo no storage e a **chave retornada** é gravada na coluna.

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.utils import LocalUploadStorage


site.register(AdminModel(
    model=DocumentModel,
    upload_fields=[DocumentModel.attachment],   # coluna String que guarda a chave
    upload_storage=LocalUploadStorage("media/"),  # ou MinIOUploadStorage(...)
))
```

- O form vira `multipart/form-data` automaticamente quando há `upload_fields`.
- **Create**: arquivo obrigatório só se a coluna for `NOT NULL` e sem default.
- **Edit**: sem arquivo novo → mantém o valor atual (mostra "Current: …"); com arquivo → substitui.
- A coluna guarda a **chave** do storage (`<slug>/<campo>/<uuid>.<ext>`); use o `upload_storage` (ou `UploadUtils`) pra servir/baixar depois.

!!! warning "`upload_fields` exige `upload_storage`"
    Registrar `upload_fields` sem `upload_storage` levanta `ValueError` na
    construção do `AdminModel` — sem storage não há onde gravar o arquivo.

!!! tip "Navegação por sidebar + burger"
    Toda página autenticada tem uma **sidebar** persistente: Dashboard, um
    link por modelo registrado (agrupados em "Models") e, com
    `show_logs=True`, "Logs" em "System". O item da página atual fica
    destacado. No **desktop** a sidebar fica sempre visível à esquerda; no
    **mobile** (≤768px) ela vira off-canvas, aberta pelo ícone **burger**
    no header e fechada tocando no scrim — tudo CSS puro, sem JS.

!!! info "Página de logs (`show_logs=True`)"
    `GET /admin/logs` lê os arquivos JSON estruturados que o
    `configure_logging(log_dir=…)` grava. Passe o **mesmo** `log_dir` para
    `make_admin_router`. A página oferece filtro por fonte
    (`all`/`debug`/`info`/`warning`/`error`/`critical`/`500`), busca por
    substring na mensagem e paginação, com badges coloridos por nível.
    É **opt-in** (`show_logs=False` por default) porque o payload expõe
    tracebacks e metadados de request — só habilite atrás do login do
    admin. Sem arquivos no `log_dir`, a página mostra um estado vazio.

!!! tip "Responsivo por padrão"
    Os templates + CSS embutidos são responsivos: em telas estreitas (≤600px) o header empilha, busca/filtros/ações viram full-width, as tabelas ganham scroll horizontal (nunca quebram o layout) e o grid do detail colapsa para uma coluna. Headers de coluna são clicáveis para alternar a ordenação (▲/▼).

#### 4. Defaults de segurança de sessão

`SignedCookieSessionStore` usa `itsdangerous.TimestampSigner` (HMAC-SHA256) para assinar um único cookie:

- `HttpOnly` sempre definido.
- `Secure` marcado quando `cookie_secure=True` (padrão; desligue no dev HTTP local).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` aceitos).
- Tempo de vida padrão `8h`; cookies expirados ou adulterados são rejeitados silenciosamente.
- Um token CSRF por sessão é gerado no login e exigido por todo POST de formulário (login, logout, criar, editar, excluir, ações em massa).
- `secret_key` deve ter ao menos 32 bytes — chaves curtas levantam `ValueError` no momento da construção.

!!! danger "Login em loop? É o `Secure` do cookie sobre HTTP puro"
    Se o `POST /admin/login` responde `303` (parece sucesso), mas o `GET /admin/`
    seguinte redireciona de volta pro login — repetindo pra sempre — o cookie de
    sessão **não está voltando**. Causa quase certa: `cookie_secure=True` enquanto
    o admin é servido por **HTTP puro** (sem TLS na frente). O browser recusa
    gravar um cookie `Secure` em conexão não-HTTPS, então nenhuma sessão persiste.

    ```python
    # ❌ Atado a DEBUG: em produção DEBUG=false → cookie_secure=True,
    #    mas se não houver HTTPS na frente, o login entra em loop.
    make_admin_router(..., cookie_secure=not settings.DEBUG)

    # ✅ Controle dedicado, independente de DEBUG:
    make_admin_router(..., cookie_secure=settings.ADMIN_COOKIE_SECURE)
    ```

    **Correção certa:** ponha HTTPS na frente (nginx/Caddy terminando TLS) e
    deixe `cookie_secure=True` — o cookie da sessão admin não deve trafegar em
    claro. **Paliativo** só quando o admin roda mesmo em HTTP (intranet, MVP):
    `cookie_secure=False`, ciente de que a sessão vai sem `Secure`. Não amarre
    esse flag ao `DEBUG` — ligar debug em produção é pior que o problema original.

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

#### 6. Customizar a aparência — `AdminTheme`

O CSS do admin é todo dirigido por **CSS custom properties** em `:root`. Em vez de forkar a folha de estilo, você passa um `AdminTheme` com **parâmetros tipados e documentados** — cores, logo, favicon, fonte, raio, rodapé, modo escuro — e a SDK injeta um bloco `<style>` no `<head>` (depois do `admin.css`, então ele vence).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # cor primária (links, botões, item ativo)
    accent_hover="#6d28d9",                 # tom de hover do accent
    header_bg="#1e1b4b",                    # fundo do header/sidebar
    radius="10px",                          # raio de botões, inputs, cards, tabelas
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # imagem no header (no lugar do texto)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # superfícies de conteúdo escuras
)

site: AdminSite = AdminSite(title="Servus Admin", brand="Servus", theme=theme)
```

`AdminTheme()` sem argumentos é um **no-op**: reproduz a aparência padrão. Você só define o que quer mudar.

!!! tip "A regra de ouro"
    Cada campo do `AdminTheme` mapeia para uma variável CSS de `:root` (ou
    para um pedaço de chrome, como o logo). É tudo tipado — o autocomplete
    do editor lista as opções e o mypy valida — e nenhuma string precisa
    ser um nome de classe CSS ou seletor.

| Campo | Tipo | Padrão | Efeito |
|-------|------|--------|--------|
| `accent` | `str` | `"#2563eb"` | Cor primária: links, botões, item ativo da sidebar |
| `accent_hover` | `str` | `"#1d4ed8"` | Tom de hover/ativo do `accent` |
| `danger` | `str` | `"#b91c1c"` | Ações destrutivas e mensagens de erro |
| `header_bg` | `str` | `"#0f172a"` | Fundo do header |
| `sidebar_bg` | `str \| None` | `None` | Fundo da sidebar (cai pra `header_bg`) |
| `page_bg` | `str \| None` | `None` | Fundo do conteúdo (padrão do modo) |
| `radius` | `str` | `"6px"` | Raio de botões, inputs, cards, tabelas |
| `font_family` | `str \| None` | `None` | `font-family` do painel inteiro |
| `logo_url` | `str \| None` | `None` | Imagem no header em vez do texto |
| `logo_alt` | `str` | `"Logo"` | `alt` da imagem do logo |
| `favicon_url` | `str \| None` | `None` | Favicon da aba |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Texto do rodapé |
| `dark_mode` | `bool` | `False` | Superfícies de conteúdo escuras |
| `custom_css_url` | `str \| None` | `None` | Folha de estilo extra, linkada por último |

!!! info "Modo escuro"
    `dark_mode=True` troca as **superfícies de conteúdo** (fundo da página,
    texto, linhas da tabela, inputs, bordas) para uma paleta escura. O
    header/sidebar já são escuros, então não mudam; `accent` e as outras
    cores continuam valendo. Um `page_bg` explícito vence o modo escuro.

!!! warning "Escape hatch para o resto"
    Para o que os campos não cobrem, aponte `custom_css_url` para a sua
    própria folha de estilo. Ela é linkada **depois** do tema, então
    sobrescreve tudo — inclusive o `AdminTheme`.

!!! danger "Valores são do desenvolvedor, não do usuário final"
    Os caracteres `< > { } "` são rejeitados em qualquer campo de texto
    (`ValueError` na construção), porque quebrariam o `<style>` injetado ou
    um atributo HTML. Nunca derive valores de `AdminTheme` de entrada de
    usuário final.

**Recap:** instancie `AdminTheme` com os campos que quer mudar, passe via
`AdminSite(theme=...)`, e a aparência muda em todas as páginas (login,
dashboard, list, detail, forms) sem tocar em CSS. Para customização total,
`custom_css_url`.
