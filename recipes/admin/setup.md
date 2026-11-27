# Montar o painel

Use para subir o painel: modelo de usuário, registro das classes de admin (ou `automap`) e o router com tudo o que ele monta.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

## 1. Modelo de usuário

Subclasse `BaseUserModel` para ganhar as quatro colunas que o backend de auth do admin espera (`email`, `hashed_password`, `is_admin`, `last_login_at`), mais o `email_verified_at` que o [fluxo de auth](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/email-change.md#o-que-conta-como-e-mail-verificado) preenche, em cima da linha padrão do `BaseModel`. Ativar uma conta pelo admin não preenche `email_verified_at`, e editar o `email` dela o limpa:

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

## 2. Registre suas classes de admin

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

## 2b. Atalho — registrar todos os modelos de uma vez (`automap`)

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

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import UserModel


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

## 3. Monte o router

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
- `GET  /admin/logs/export` — **export dos logs** (`?format=md|json`): baixa a seleção filtrada como markdown (traceback em bloco cercado, pronto para colar em issue) ou JSON verbatim.
- `GET  /admin/m/{slug}/` — list view com paginação + busca em texto livre (`?q=`: cada palavra procurada por trecho, sem diferenciar maiúscula, em qualquer um dos `search_fields`, e toda palavra precisa aparecer; `%` e `_` são literais) + filtros por campo (`?filter_<field>=value`) + **ordenação por coluna** clicável (`?sort=<coluna>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` / `export.xlsx` (este com o extra `[spreadsheet]`) — **exporta** o resultado atual (respeitando busca/filtros/ordenação) como CSV ou JSON. Limite de linhas via `make_admin_router(export_max_rows=…)` (default 5000). No CSV, uma célula de texto que começa com `=`, `+`, `-`, `@`, tab ou CR sai com um `'` na frente, para a planilha mostrar o texto em vez de avaliar uma fórmula; o JSON sai sem mudança.
- `POST /admin/m/{slug}/bulk` — **ações em massa** (delete / activate / deactivate + suas **ações customizadas**) nas linhas selecionadas.
- `GET/POST /admin/m/{slug}/new` — **criar** registro (quando `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view com botões Edit/Delete.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **editar** registro (quando `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **excluir** registro (quando `can_delete`).
- `GET/POST /admin/m/{slug}/import` + `POST /admin/m/{slug}/import/confirm` — **importar** CSV/XLSX com prévia (quando `can_import`).
- `GET  /admin/m/{slug}/{identity}/duplicate` — **duplicar** (quando `can_duplicate`).
- `GET  /admin/m/{slug}/{identity}/restore/{entry_id}` — **restaurar** uma mudança do histórico (com `audit_model`).
- `GET/POST /admin/m/{slug}/{identity}/impersonate` — **entrar como** a conta da linha (com `impersonate=` e a permissão `IMPERSONATE`).
- `GET  /admin/search` — **busca global** nos models com `search_fields`.
- `GET  /admin/static/{path}` — o CSS embutido do painel (`admin.css`).
- `GET  /admin/_ssr/htmx.js` — o HTMX empacotado no SDK (`ssr/_static/htmx.min.js`), servido sem sessão porque a página de login também o carrega. O caminho segue o `prefix`: com `prefix="/backoffice"`, vira `/backoffice/_ssr/htmx.js`.

!!! info "O painel não depende de terceiro para renderizar"
    Nenhuma página do admin busca script, folha de estilo ou fonte fora da sua aplicação: o `<script>` do HTMX aponta para `{prefix}/_ssr/htmx.js`, que serve byte a byte o arquivo empacotado no wheel. A exceção é o que você mesmo configurar: `favicon_url`, `logo_url` e `custom_css_url` do `AdminTheme` são carregados de onde apontarem. Indisponibilidade ou adulteração de uma CDN não alcança a origem do admin, que é a mesma do `POST /admin/sql` e do CRUD. Funciona só com o extra `[admin]` — o `[ssr]` não é necessário.

    **CSP continua sendo um passo à parte.** O admin não envia `Content-Security-Policy`, e ainda usa `<style>` inline (o tema do `AdminTheme`), um `<script>` inline na list view, um `onclick` no autocomplete de FK e atributos `style=` nas barras de progresso. Servir o HTMX localmente tira a dependência de terceiro, mas não basta para uma política `script-src 'self'` sem `'unsafe-inline'`: com ela, essas partes param de funcionar.

!!! info "Escrita (CRUD) + permissões"
    Create/edit/delete são controlados por flags no `AdminModel`: `can_create` / `can_edit` / `can_delete` (todas `True` por default; uma view desativada responde `404`). Todo POST de escrita carrega o token CSRF da sessão, validado no servidor (`403` em mismatch). Os **widgets de campo** são derivados do tipo da coluna — texto / textarea (strings longas) / number / checkbox / `datetime-local` / date / `select` para enums — com validação de obrigatórios + erros por campo re-renderizados no formulário. Escrita que o **banco** recusa (unique, FK, `NOT NULL`) volta pelo mesmo caminho: `400` com a mensagem do repositório (`Conflict creating <Model>`) no topo do form, nunca `500`.

    **Ações em massa**: a list view mostra checkboxes por linha + select-all e uma barra de ação (delete / activate / deactivate) que opera nas linhas marcadas via `POST .../bulk` (CSRF + flags `can_delete`/`can_edit`), apoiada em `BaseRepository.delete_batch` / `bulk_update`.

    **FK-select**: uma coluna FK cujo destino tem `AdminModel` registrado vira um dropdown das linhas relacionadas (igual ao FK select do Django) no formulário, em vez de um input UUID cru. O label da opção vem do primeiro `search_fields` do admin referenciado (fallback: atributo `name`/`title`/`email`, depois o id). Limitado a 1000 linhas; FK para tabela não-gerenciada continua input UUID.

    **MFA no login**: um principal com MFA habilitado (colunas `totp_secret`/`totp_enabled_at` do `MFAMixin`) passa por um desafio TOTP em `/admin/mfa` depois da senha — só um código válido libera o acesso. Habilite passando um usuário com MFA via `UserModelAuthBackend(UserModel, mfa_issuer=...)`; backends customizados sobrescrevem `mfa_enabled`/`verify_mfa`.

    Cada código vale **uma** entrada: o `UserModelAuthBackend` grava o passo aceito em `totp_last_step` (coluna do `MFAMixin`; backend customizado que guarda o próprio estado sobrescreve `claim_mfa_step`). E o desafio tem limite de códigos errados por principal: `make_admin_router(mfa_throttle=...)`, por padrão 5 falhas em 900 s num `InMemoryThrottleBackend`; esgotado, `/admin/mfa` responde `429` com `Retry-After` antes de olhar o código, mesmo depois de um login novo. Com mais de um worker, passe um `AttemptThrottle` sobre o Redis.

    **Audit trail**: create/edit pelo admin carimba `created_by`/`updated_by` (do `AuditMixin`) com o id do admin atuante; o detail mostra um painel **Audit** com timestamps e — quando o modelo tem as colunas de auditoria — o ator (UUID resolvido para nome via o auth backend). Modelos sem `AuditMixin` mostram só os timestamps.

    Edição inline in-place dos filhos: veja `Inline(editable=True)` mais abaixo.
