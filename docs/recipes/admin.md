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

Cobre também edição inline in-place dos filhos 1-N (`Inline(editable=True)`).

Requer o extra `[admin]`:

```bash
uv add "tempest-fastapi-sdk[admin]"
```

#### 1. Modelo de usuário

Subclasse `BaseUserModel` para ganhar as quatro colunas que o backend de auth do admin espera (`email`, `hashed_password`, `is_admin`, `last_login_at`), mais o `email_verified_at` que o [fluxo de auth](auth-flow.md#o-que-conta-como-e-mail-verificado) preenche, em cima da linha padrão do `BaseModel`. Ativar uma conta pelo admin não preenche `email_verified_at`, e editar o `email` dela o limpa:

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
- `GET  /admin/logs/export` — **export dos logs** (`?format=md|json`): baixa a seleção filtrada como markdown (traceback em bloco cercado, pronto para colar em issue) ou JSON verbatim.
- `GET  /admin/m/{slug}/` — list view com paginação + busca em texto livre (`?q=`: cada palavra procurada por trecho, sem diferenciar maiúscula, em qualquer um dos `search_fields`, e toda palavra precisa aparecer; `%` e `_` são literais) + filtros por campo (`?filter_<field>=value`) + **ordenação por coluna** clicável (`?sort=<coluna>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **exporta** o resultado atual (respeitando busca/filtros/ordenação) como CSV ou JSON. Limite de linhas via `make_admin_router(export_max_rows=…)` (default 5000). No CSV, uma célula de texto que começa com `=`, `+`, `-`, `@`, tab ou CR sai com um `'` na frente, para a planilha mostrar o texto em vez de avaliar uma fórmula; o JSON sai sem mudança.
- `POST /admin/m/{slug}/bulk` — **ações em massa** (delete / activate / deactivate + suas **ações customizadas**) nas linhas selecionadas.
- `GET/POST /admin/m/{slug}/new` — **criar** registro (quando `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view com botões Edit/Delete.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **editar** registro (quando `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **excluir** registro (quando `can_delete`).
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
    EmailUtils,
    admin_action,
)

from src.admin import site
from src.core.settings import settings
from src.db.models import UserModel

mailer = EmailUtils(**settings.email_kwargs())


@admin_action(label="Enviar boas-vindas")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Roda nas linhas selecionadas; a mensagem é exibida na list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send(
            user.email,
            subject="Bem-vindo",
            body=f"Olá, {user.email}! Sua conta está pronta.",
        )
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

O painel **commita** a sessão do request quando o handler retorna, com ou
sem `secrets` no resultado, e faz rollback quando ele levanta. Então a ação
pode mudar a linha direto na sessão, sem commit próprio:

```python
from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


@admin_action(label="Confirmar e-mail")
async def verify_email(ctx: AdminActionContext) -> AdminActionResult:
    """Marca o e-mail como confirmado; o painel commita no fim."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} conta(s) confirmada(s).")
```

!!! warning "Antes da 0.366.0, a ação sem `secrets` não era commitada"
    O commit só acontecia no ramo com `secrets`: uma ação que gravava pela
    sessão mostrava o flash de sucesso e perdia a gravação no fim do request
    (#784).

Cada opção do select de ações em massa leva o próprio texto de confirmação.
Ação comum (e Activate/Deactivate) pergunta "Apply the selected action to the
checked rows?"; ação `dangerous=True` (e o Delete embutido) nomeia a ação e
avisa que não tem volta — `Run "Recusar e devolver" on the checked rows? This
cannot be undone.`. O rótulo vai num atributo `data-confirm` escapado pelo
template, nunca interpolado no JavaScript, então aspas ou `<` no rótulo não
quebram a página.

### Mostrar um segredo uma vez (`secrets=`)

Algumas ações **criam uma credencial**: uma chave de API, um token de
integração, uma senha temporária. O operador precisa ver o valor uma vez, e
o flash não serve para isso, porque ele volta pelo redirect na query string
(`?flash=...`). Ali o valor iria para o histórico do browser, para o access
log e para o `Referer` da próxima página.

Devolva o valor em `AdminActionResult(secrets=[...])`, como `AdminSecret`:

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminSecret,
    admin_action,
    generate_opaque_token,
)

from src.admin import site
from src.db.models import ApiKeyModel, UserModel


@admin_action(label="Gerar chave de API")
async def issue_api_key(ctx: AdminActionContext) -> AdminActionResult:
    """Cria uma chave por usuário marcado e mostra cada uma uma vez."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    secrets: list[AdminSecret] = []
    for user in users:
        plaintext, digest = generate_opaque_token()
        ctx.db_session.add(ApiKeyModel(user_id=user.id, key_hash=digest))
        secrets.append(AdminSecret(f"Chave de {user.email}", plaintext))
    return AdminActionResult(f"{len(secrets)} chave(s) criada(s).", secrets=secrets)


site.register(AdminModel(model=UserModel, actions=[issue_api_key]))
```

Com `secrets`, o painel **não redireciona**. A resposta do próprio `POST`
é uma página com a mensagem e cada valor num campo só de leitura (o foco já
seleciona o texto para copiar), e com um botão de volta para a lista. O
que muda em relação ao flash:

- **Nada vai para URL nem header.** O valor só existe no corpo dessa
  resposta.
- **`Cache-Control: no-store`** e `Pragma: no-cache`: nem o browser nem um
  proxy guardam cópia da página. Recarregar reenvia o `POST`, mas o form
  da lista leva um token de uso único e o reenvio não roda a ação de novo
  (detalhe em [Recarregar não emite de novo](#recarregar-nao-emite-de-novo)).
- **`Referrer-Policy: no-referrer`**: o link de volta não carrega a URL da
  ação.
- **A sessão do request recebe `commit` antes de a página renderizar.** O
  `ctx.db_session.add(...)` acima não precisa de commit próprio, e um
  segredo na tela nunca é de uma linha que voltou atrás.
- **`repr(AdminSecret)` omite o valor**, então logar o resultado não vaza
  o segredo.

!!! warning "Guarde só o digest"
    O painel mostra o texto puro uma vez, mas guardá-lo é decisão sua.
    Persista o `digest` do `generate_opaque_token()` (SHA-256) e confira
    a chave recebida com `verify_opaque_token`. A coluna do digest vai em
    `__secret_columns__`, para nenhuma tela do painel exibi-la.

Ação sem `secrets` continua igual: flash na list view, por redirect.

### Pedir dados antes de rodar (`schema=`) *(v0.366.0+)*

Algumas ações precisam de um dado do operador: o **motivo** de um acesso
auditado, um valor, uma data. Passe um model Pydantic em `schema=` e o
handler passa a receber `(data, ctx)`:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


class VerifyEmailInput(BaseModel):
    """O que o operador preenche antes da ação rodar."""

    reason: str = Field(min_length=10, max_length=500)


@admin_action(label="Confirmar e-mail", schema=VerifyEmailInput)
async def verify_email(
    data: VerifyEmailInput, ctx: AdminActionContext
) -> AdminActionResult:
    """Confirma as contas marcadas e grava o motivo na trilha."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} confirmada(s): {data.reason}")
```

O fluxo, medido em `tests/admin/test_custom_action_flow.py`:

1. Escolher a ação na lista **não roda nada**: o `POST /m/{slug}/bulk`
   responde `200` com o formulário gerado do schema (o mesmo gerador do
   `create_handler=`), o texto `2 row(s) selected.`, os ids em campos ocultos
   e um nonce de uso único.
2. Envio inválido responde `400` com o erro no campo
   (`String should have at least 10 characters`) e o que foi digitado; o
   handler não roda e o nonce não é gasto.
3. Envio válido roda o handler com o dado validado e os ids, commita e
   volta para a lista com o flash (`303`). Reenviar o mesmo formulário não
   roda de novo: o nonce já foi gasto, e o painel avisa
   `This form was already submitted; it did not run again.`.
4. Uma `AppException` do handler faz rollback e re-renderiza o formulário
   com a mensagem e o que foi digitado, com `400`.

Ação sem `schema=` continua igual: roda direto da lista.

### Criar por service (`create_handler=`)

Uma chave de API não nasce das colunas: o service gera o segredo, grava só o
hash e mostra o texto puro uma vez. O form de colunas pediria `key_hash` e
`key_prefix` digitados à mão — errado. Levar a emissão para uma ação em lote
na lista de **usuários** funciona, mas quem abre **Api Keys** não acha botão
nenhum, e a ação não tem campos para nome e validade.

`create_handler` troca o form de colunas por um form gerado de um **schema
Pydantic** e um handler que recebe o schema validado:

```python
from uuid import UUID

from pydantic import BaseModel, Field
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminModel,
    AdminSecret,
    AdminSite,
    generate_opaque_token,
)

from src.db.models import ApiKeyModel


class ApiKeyAdminCreateSchema(BaseModel):
    name: str = Field(title="Nome da chave", min_length=3)
    expires_in_days: int | None = Field(default=None, title="Validade (dias)")
    user_id: UUID | None = Field(default=None, title="Usuário")


async def issue_api_key(
    data: ApiKeyAdminCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    plaintext, digest = generate_opaque_token()
    ctx.db_session.add(
        ApiKeyModel(name=data.name, key_hash=digest, user_id=data.user_id)
    )
    return AdminActionResult("Chave emitida", secrets=[AdminSecret(data.name, plaintext)])


site = AdminSite(title="Admin")
site.register(
    AdminModel(
        ApiKeyModel,
        autocomplete_fields=["user_id"],
        create_handler=AdminCreateHandler(
            schema=ApiKeyAdminCreateSchema,
            handler=issue_api_key,
            label="Nova chave de API",
        ),
    )
)
```

- O botão da lista vira **+ Nova chave de API** e abre o form do schema:
  `title` vira o rótulo, o tipo escolhe o controle (enum vira select, `int`
  vira número, `SecretStr` vira senha), campo sem default é obrigatório. Um
  campo cujo nome está em `autocomplete_fields` usa a mesma busca de FK do
  form de colunas.
- Erro de validação re-renderiza com `400`, a mensagem de cada campo e o que
  foi digitado. Uma `AppException` levantada pelo handler volta como erro do
  form, também com o que foi digitado, e a sessão é desfeita.
- Com `secrets`, a resposta é a do próprio `POST`, como na ação acima. Sem
  eles, o painel redireciona para a lista com a mensagem.
- `create_handler` substitui o form de colunas: `can_create=True` junto
  levanta `ValueError` na construção.

#### Recarregar não emite de novo

O form leva um token de uso único, gasto logo antes de o handler rodar. Um
reload da página do segredo reenvia o `POST`, e o painel responde com `303`
para a lista e o aviso *This form was already submitted; it did not run
again.*, sem chamar o handler. Medido no Chromium: o reload reenviou o
`POST`, caiu na lista com o aviso e a tabela ficou com **uma** chave.

O mesmo vale para as ações em lote da lista, que agora levam o token. Um
`POST` sem o token (um script, um template próprio) roda como antes, uma vez
por envio. O token é guardado no mesmo backend das revogações de sessão: com
o `InMemoryThrottleBackend` padrão ele vale por processo; atrás de mais de um
worker, passe o cliente Redis em `AdminSessionRevocations` para o reenvio ser
recusado em qualquer um.

## Campo de escolha numa coluna `String` (`choices=`)

Uma coluna `Mapped[MeuEnum]` já aparece no form como `<select>`. Uma coluna
`String` que guarda o valor de um enum, ou um vocabulário que o painel deve
restringir, aparece como texto livre, porque o admin não tem como saber dos
valores. É o caso do `kind` da carteira: a coluna é `String(64)` porque a
aplicação grava os próprios tipos ("SALE", "TIP") ao lado dos do SDK.

`choices=` diz ao admin quais valores aceitar, sem mudar a coluna:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite
from tempest_fastapi_sdk.wallet.models import make_wallet_entry_model
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKindEnum

WalletEntryModel = make_wallet_entry_model()

site = AdminSite(title="Carteira")
site.register(
    AdminModel(
        WalletEntryModel,
        choices={
            "kind": WalletEntryKindEnum,
            "reference_type": [("order", "Pedido"), ("payout", "Saque")],
        },
    )
)
```

- **Cada entrada** é uma classe `Enum` (o valor de cada membro, com o nome
  humanizado como rótulo), uma lista de strings, ou de pares
  `(valor, rótulo)`.
- **O form vira `<select>`**: "Kind" mostra Withdraw, Withdraw refund,
  Reversal e Opening balance; "Reference type", sendo opcional, ganha
  "— none —" na frente.
- **O servidor confere.** Um POST com valor fora da lista volta `400` com
  "Choose one of the listed values for Kind." e não grava nada; o select não
  é a única barreira.
- **Nome que não é coluna** (um typo) levanta `ValueError` na construção, e
  lista vazia também.
- **No filtro da lista**: a coluna que também está em `list_filter` ganha o
  mesmo `<select>`, com os mesmos rótulos, em vez de uma caixa de texto livre.

!!! tip "Sem Activate / Deactivate"
    Em tabela onde `is_active` não significa nada (um registro financeiro,
    por exemplo), `AdminModel(can_toggle_active=False)` tira as duas ações em
    massa da lista e recusa com `400` um `action=deactivate` montado à mão.

!!! tip "Vocabulário aberto"
    Se a aplicação grava tipos próprios no `kind`, junte-os aos do SDK numa
    lista: `choices={"kind": [*WalletEntryKindEnum, "SALE", "TIP"]}` vira as
    seis opções, cada uma com o próprio valor como rótulo.

## Campo de upload de arquivo / imagem

Uma coluna `String` que guarda o caminho/chave de um arquivo pode virar um
**input de upload** no formulário. Liste a coluna em `upload_fields` e
passe um `upload_storage` (os backends que o SDK já tem —
`LocalUploadStorage` / `MinIOUploadStorage`). No submit, o arquivo é
salvo no storage e a **chave retornada** é gravada na coluna.

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.utils import LocalUploadStorage

from src.admin import site
from src.db.models import DocumentModel


site.register(AdminModel(
    model=DocumentModel,
    upload_fields=[DocumentModel.attachment],   # coluna String que guarda a chave
    upload_storage=LocalUploadStorage("media/"),  # ou MinIOUploadStorage(...)
))
```

!!! info "Instalação"
    Os backends de upload não vêm com o `[admin]`. `LocalUploadStorage`
    depende do extra `[upload]` — `uv add "tempest-fastapi-sdk[upload]"`
    (traz `aiofiles`); `MinIOUploadStorage` depende do `[minio]` —
    `uv add "tempest-fastapi-sdk[minio]"` (traz `minio`).

- O form vira `multipart/form-data` automaticamente quando há `upload_fields`.
- **Create**: arquivo obrigatório só se a coluna for `NOT NULL` e sem default.
- **Edit**: sem arquivo novo → mantém o valor atual (mostra "Current: …"); com arquivo → substitui.
- A coluna guarda a **chave** do storage (`<slug>/<campo>/<uuid>.<ext>`); a página do registro tem o link **Download** dela.

!!! warning "`upload_fields` exige `upload_storage`"
    Registrar `upload_fields` sem `upload_storage` levanta `ValueError` na
    construção do `AdminModel` — sem storage não há onde gravar o arquivo.

### Só um tipo de arquivo (`upload_accept=`)

`upload_accept` diz que tipos um campo de upload aceita. O input ganha
`accept=` (o seletor de arquivos do browser já filtra) e o servidor confere
antes de gravar: o tipo declarado tem de estar na lista **e**, quando a
lista tem algum tipo que o `sniff_mime` reconhece pelos bytes
(`SNIFFABLE_MIMETYPES`: PDF, PNG, JPEG, GIF, BMP, WebP, HEIC), o começo do
arquivo tem de ser de um tipo da lista. Um PNG renomeado para `.pdf` é
recusado com erro no campo, e nada vai para o storage.

!!! warning "A conferência é da lista, não de cada tipo"
    Misturar um tipo sem assinatura com um que tem — `["text/plain",
    "application/pdf"]` — recusa texto puro: texto não tem bytes mágicos,
    então nunca "sniffa" para um tipo da lista. Medido chamando a checagem do
    router: `text/plain` com `hello world` é recusado nessa lista e aceito
    em `["text/plain"]` sozinho.

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel
from tempest_fastapi_sdk.utils import LocalUploadStorage


class InvoiceModel(BaseModel):
    """An invoice with its PDF."""

    __tablename__ = "invoices"

    pdf_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


invoices: AdminModel[InvoiceModel] = AdminModel(
    InvoiceModel,
    upload_fields=["pdf_key"],
    upload_storage=LocalUploadStorage("media/"),
    upload_accept={"pdf_key": ["application/pdf"]},
)
```

Na página do registro, cada campo de upload preenchido mostra o link
**Download**: o storage que gera URL temporária (`MinIOUploadStorage`)
redireciona para ela; o `LocalUploadStorage` entrega o arquivo como anexo,
com `X-Content-Type-Options: nosniff`.

### Regra que depende da linha (`validate_edit=`)

`validate_edit(row, form)` roda no submit da edição, antes de qualquer
upload ser gravado. Devolver `{"campo": "mensagem"}` recusa a edição com
esses erros — é o lugar de uma regra como "o comprovante de um registro
finalizado não muda". O preset abaixo usa exatamente isso.

### Campo trancado por linha (`readonly_fields_for=`)

`readonly_fields` tranca o mesmo campo em toda linha. Quando o campo só pode
mudar enquanto a linha está num estado — o motivo de uma recusa, editável
até o registro ser finalizado —, passe `readonly_fields_for(row)`: ele
recebe a linha guardada e devolve os campos a trancar **nela**, somados aos
de `readonly_fields`. A trava vale nos dois lados, do mesmo jeito que a
estática: o campo sai do formulário de edição daquela linha, e um valor
postado para ele à mão é ignorado no salvamento (a resposta é o `303` de
sempre, e o valor guardado não muda).

```python
from collections.abc import Sequence

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel


class TicketModel(BaseModel):
    __tablename__ = "tickets"

    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)


def note_locked_when_closed(row: TicketModel) -> Sequence[str]:
    return ["note"] if row.status == "closed" else []


tickets: AdminModel[TicketModel] = AdminModel(
    TicketModel,
    readonly_fields=["status"],
    readonly_fields_for=note_locked_when_closed,
)
```

!!! note "O que ele não cobre"
    Campo de upload e de senha é tratado à parte no formulário, então
    tranque-o com `validate_edit`. E o gancho vale para o formulário de
    edição da própria linha, não para o formset inline do pai.

### Preset: saque manual (`manual_payout_admin`)

`manual_payout_admin(ManualPayoutModel, upload_storage=...)` devolve o
`AdminModel` da tabela de saques pagos à mão (`make_manual_payout_model`):
valores só leitura, comprovante só em PDF, link de download e a ação
**Marcar como pago**, que só conclui linha `PENDING` com comprovante.

Com `ledger_factory=` (uma função que recebe a sessão do painel e devolve o
`WalletLedger` sobre ela), entra também **Recusar e devolver**: o
administrador escreve o motivo no campo `rejection_reason` da linha e roda a
ação, que deixa a linha `REJECTED` e devolve o valor à carteira numa linha
`WITHDRAW_REFUND`, no mesmo commit. Linha sem motivo fica `PENDING` e a
mensagem lista o `correlation_id` dela; linha que já saiu de `PENDING` não
muda, e o campo do motivo sai do formulário de edição dela
(`readonly_fields_for`). As duas ações chamam
`ManualPayoutService` e rodam os `hooks=` que você passar — veja
[Carteira » Concluir ou recusar o saque à mão](wallet.md#concluir-ou-recusar-o-saque-a-mao-manualpayoutservice).

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseUserModel
from tempest_fastapi_sdk.admin import AdminModel, manual_payout_admin
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletLedger,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


def ledger(session: AsyncSession) -> WalletLedger:
    return WalletLedger(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


payouts: AdminModel[Any] = manual_payout_admin(
    ManualPayoutModel,
    upload_storage=LocalUploadStorage("receipts"),
    ledger_factory=ledger,
)
```

O fluxo inteiro — o saque que grava a linha e manda o e-mail ao
administrador — está em
[Mercado Pago: testando webhooks » Saque manual](mercado-pago-webhooks.md#saque-manual).

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

    O botão **Refresh** recarrega a página com a mesma fonte e a mesma busca,
    voltando à primeira página, que é a dos registros mais recentes. A página
    lê os arquivos a cada request, então recarregar é o que basta; o admin
    não carrega JavaScript, e o botão é um link. Abaixo do título, **Read at**
    mostra quando os arquivos foram lidos (UTC), para ficar visível que a
    leitura rodou mesmo quando nada novo chegou. O botão aparece também no
    estado vazio, que é justamente quando se espera o primeiro registro.

!!! tip "Traceback de erros 500 e export para issue"
    Cada registro que carrega um traceback (os handlers do SDK logam com
    `exc_info=True`) vira um item clicável: **a própria mensagem é o
    gatilho**, então clicar em qualquer ponto da entrada revela o trace,
    com os campos de correlação do request (`path`, `method`,
    `status_code`, `request_id`) ao lado. `<details>`/`<summary>` puro, sem
    JS, recolhido por default para que uma página cheia de 500 continue
    escaneável — registro sem traceback não ganha gatilho nenhum.

    `GET /admin/logs/export?format=md|json` baixa a seleção **filtrada**
    (mesmos `?source=` e `?q=` da página), do mais recente para o mais
    antigo, até 500 registros:

    - **`format=md`** — cada traceback vai num bloco cercado ```` ```pytb ````,
      então sobrevive a um colar em issue/PR com a indentação intacta. O
      cabeçalho declara fonte, contagem, filtro aplicado e — quando o teto
      de 500 corta — quantos registros casavam no total, para um export
      parcial nunca se passar por completo.
    - **`format=json`** — os registros verbatim, com todo campo que a
      aplicação logou via `extra=`, para consumo por ferramenta.

    Em telas abaixo de 600px a tabela vira **cards empilhados** (o header
    é escondido e cada célula se nomeia sozinha), então a mensagem e o
    traceback ficam na viewport sem scroll horizontal. As outras list views
    mantêm o scroll lateral, que serve para lista que se folheia.

    O export herda o gate de sessão do admin: traceback é exatamente o
    payload que não pode ficar público. Para montar seu próprio export,
    `render_entries_markdown` e `render_entries_json` são exportados no
    nível do pacote.

!!! tip "Responsivo por padrão"
    Os templates + CSS embutidos são responsivos: em telas estreitas (≤600px) o header empilha, busca/filtros/ações viram full-width, as tabelas ganham scroll horizontal (nunca quebram o layout) e o grid do detail colapsa para uma coluna. Headers de coluna são clicáveis para alternar a ordenação (▲/▼).

## Histórico de auditoria no detail (`audit_model=`)

O painel já carimba `created_by` / `updated_by`. Para ver **o quê** mudou
(não só quem/quando), passe um `audit_model` — a mesma tabela
`BaseAuditLogModel` que o `BaseRepository` já escreve — e o detail ganha
uma **timeline** por registro.

Primeiro, a tabela de auditoria e um repository que a alimenta:

```python
# src/db/models/audit.py
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLog(BaseAuditLogModel):
    __tablename__ = "audit_log"
```

```python
# grave a trilha nas escritas (create/update/delete)

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.db.audit import snapshot_model

from src.db.models import AuditLog, OrderModel, UserModel

current_user = UserModel(email="ana@example.com")
# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))


repo = BaseRepository(session, model=OrderModel, audit_model=AuditLog)


async def main() -> None:
    """Run this example."""
    order = await repo.add_audited(OrderModel(...), actor=str(current_user.id))

    before = snapshot_model(order)
    order.status = "shipped"
    await repo.update_audited(order, before, actor=str(current_user.id))


asyncio.run(main())
```

Então plugue o mesmo `audit_model` no admin:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import AuditLog, OrderModel


site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
```

No detail de cada pedido aparece um bloco **History**: uma entrada por
mudança (`create` / `update` / `delete`, com cor), ator e data, e um diff
campo-a-campo (antes → depois). As 50 entradas mais recentes, da mais
nova para a mais antiga.

!!! info "Como o match é feito"
    A viewer busca as linhas de auditoria onde `entity` == nome do modelo
    (ex.: `"OrderModel"`) e `entity_id` == id do registro — exatamente o
    que `add_audited` / `update_audited` / `delete_audited` gravam. Sem
    `audit_model`, o detail continua igual (só os carimbos
    `created_by`/`updated_by`).

### Atividade recente no dashboard (`recent_activity=`)

A linha do tempo acima é por registro. Para ver o que mudou no painel
inteiro sem abrir registro por registro, ligue o feed do dashboard no
`AdminSite`:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import AuditLog, OrderModel, TicketModel

site = AdminSite(title="Loja", recent_activity=20)
site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
site.register(AdminModel(model=TicketModel, audit_model=AuditLog))
```

O dashboard ganha o bloco **Atividade recente**: as últimas 20 entradas de
todos os models que declaram `audit_model`, mescladas da mais nova para a
mais antiga, cada uma com a ação, o model, o rótulo do registro (link para
o detail), a data e o autor.

O feed passa pelas mesmas regras do resto do painel:

- model em que o principal não tem `VIEW` no `access_policy` não aparece;
- entrada de uma linha fora do `row_scope` do model não aparece — nem o
  rótulo. Num model com escopo, também some a entrada de uma linha que não
  existe mais (um `delete`), porque nada prova que ela era alcançável; sem
  escopo, ela aparece sem link;
- o autor segue a regra do painel de auditoria do detail: com `row_scope`
  no model de usuário, o nome só aparece para quem o principal alcança;
  senão, o id.

!!! info "Desligado por padrão — o custo"
    `recent_activity=None` é o padrão porque o feed custa consultas em
    **toda** carga do dashboard: uma por tabela de auditoria, uma por model
    auditado (para manter só as linhas do escopo e ler os rótulos) e uma
    busca de autor por autor distinto. Para compensar as entradas que o
    escopo esconde, o painel lê até `10 × recent_activity` entradas por
    tabela; se o escopo esconder mais que isso entre as mais novas, o feed
    mostra menos que o pedido.

## FK com autocomplete (`autocomplete_fields=`)

Por padrão um campo FK cujo alvo tem admin registrado vira um `<select>`
com **todas** as linhas (capado em 1000). Numa tabela grande isso é
inutilizável. Passe `autocomplete_fields` e o campo vira uma **caixa de
busca** (HTMX) que consulta o alvo sob demanda:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Company, Employee


site.register(AdminModel(model=Company, search_fields=[Company.name]))
site.register(
    AdminModel(model=Employee, autocomplete_fields=[Employee.company_id])
)
```

No form de Employee, `company_id` deixa de listar todas as empresas:
você digita, o endpoint `/admin/m/employee/autocomplete/company_id?q=…`
busca nos `search_fields` do admin de `Company` (ILIKE, OR, até 20
resultados) e mostra as opções; clicar fixa o id no campo. No edit, o
rótulo da empresa atual já vem preenchido.

!!! info "Requisitos"
    O alvo do FK precisa ter um `AdminModel` registrado (são os
    `search_fields` dele que guiam a busca). Sem `autocomplete_fields`, o
    FK continua um `<select>` (útil para tabelas pequenas).

## Inlines — filhos 1-N no detail (`inlines=`)

Para ver (e chegar a) os registros que apontam pra este — pedidos de um
cliente, membros de um time — declare `inlines`. O detail do pai passa a
listar cada relação numa tabela, com link pro admin do filho e um botão
**Add** que já pré-preenche o FK do pai.

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[Inline(Member, Member.team_id, list_display=[Member.name])],
    )
)
site.register(AdminModel(model=Member))   # o filho precisa de admin p/ os links
```

No detail de um Team aparece a tabela **Member** com seus membros; "Add"
abre o create de Member já com `team_id` preenchido (via query param). As
colunas saem do `list_display` do `Inline` (ou, se omitido, do admin do
filho). Até 50 linhas por inline.

O modelo filho precisa ter um `AdminModel` registrado para os links
funcionarem; sem ele, as linhas aparecem só-leitura.

### Edição in-place (`editable=True`)

Para editar os filhos **sem sair do detail do pai**, passe
`editable=True` (e `can_delete=True` pra permitir remoção). A tabela vira
um formset: uma linha de inputs por filho existente + uma linha em branco
pra adicionar mais uma.

```python
from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[
            Inline(
                Member,
                Member.team_id,
                editable=True,
                can_delete=True,
            )
        ],
    )
)
site.register(AdminModel(model=Member))   # can_edit / can_delete valem aqui
```

Ao salvar (`POST /admin/m/<pai>/<id>/inlines/<filho>`), tudo acontece numa
transação: linhas existentes são atualizadas, uma linha em branco com
qualquer valor vira um filho novo, e uma caixa de exclusão marcada remove
a linha.

!!! tip "Transparente, sem mágica"
    - O **FK do pai é implícito** — forçado ao pai, nunca é um input; o
      usuário não escolhe (nem consegue reapontar) o pai errado.
    - Cada linha é **escopada ao pai**: um filho cujo FK não bate é
      ignorado, nunca editado por tabela.
    - Colunas de **upload/autocomplete** ficam no form próprio do filho
      (não entram no formset compacto).
    - Erros de validação **re-renderizam o formset in-place**, com
      mensagem por campo e sem inserir nada.
    - Precisa do `AdminModel` do filho + `can_edit` (e `can_delete` pra
      excluir). Até 50 linhas por inline.

## Muitos-para-muitos no form (`m2m_fields=`)

Um `relationship(..., secondary=...)` — tags de um artigo, papéis de um
usuário — não é coluna, então o form não o mostrava. Declare em
`m2m_fields`:

```python
from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from tempest_fastapi_sdk import AdminModel, BaseModel

from src.admin import site

article_tags = Table(
    "article_tags",
    BaseModel.metadata,
    Column("article_id", ForeignKey("articles.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Tag(BaseModel):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64))


class Article(BaseModel):
    __tablename__ = "articles"
    title: Mapped[str] = mapped_column(String(120))
    tags: Mapped[list[Tag]] = relationship(secondary=article_tags)


site.register(AdminModel(model=Tag, search_fields=[Tag.name]))
site.register(AdminModel(model=Article, m2m_fields=[Article.tags]))
```

O create e o edit ganham um `<select multiple>` com as tags (rotuladas
como no `<select>` de FK: o primeiro `search_fields` do admin do alvo),
e o detail lista as tags ligadas, com link quando o alvo tem admin. Salvar
**substitui o conjunto** na mesma transação do registro: o que não veio
selecionado é desligado, e se o save volta (validação, escopo, conflito),
nenhum vínculo muda.

Com escopo, a regra é a da FK na escrita:

- as opções são só as linhas que o principal alcança pelo `row_scope` do
  alvo (e pedem `VIEW` nele — sem `VIEW`, o campo nem aparece, e os
  vínculos gravados ficam como estão);
- um vínculo que o operador **não** alcança não aparece no form e
  **continua** depois do save — ele não apaga o que não vê;
- um id postado fora do escopo, ou que não existe, recusa o campo com
  `Select a valid choice; that record does not exist.`, o mesmo erro, para
  um `POST` forjado não descobrir ids de outro tenant.

!!! info "Conferido na construção"
    Um nome em `m2m_fields` que não é `relationship` com `secondary`
    (uma coluna, um 1-N) levanta `ValueError` quando o `AdminModel` é
    criado. O 1-N tem o [`inlines=`](#inlines-filhos-1-n-no-detail-inlines);
    o N-1 já é o `<select>` de FK. As opções param em `FK_OPTION_CAP`
    (1000) linhas, lidas com `LIMIT` no SQL; um vínculo alcançável que o
    teto deixou de fora entra como mais uma opção, já marcada, como na FK
    de seleção única — salvar sem mexer no campo não o apaga. Essas opções
    extras também param no teto, lidas com o escopo na mesma consulta; um
    vínculo que nem assim aparece continua gravado: o save só desliga o que
    o form ofereceu e voltou desmarcado.

!!! info "Campo trancado"
    Um nome de `m2m_fields` em `readonly_fields`, ou no que
    `readonly_fields_for` devolve para a linha, sai do form e o save não
    mexe nos vínculos dele.

## Contagem de relacionados na listagem (`related_counts=`)

Na listagem de clientes, quantos pedidos cada um tem? Declare no admin do
**pai** a coluna FK do **filho**:

```python
from tempest_fastapi_sdk import AdminModel, RelatedCount

from src.admin import site
from src.db.models import CustomerModel, OrderModel


site.register(
    AdminModel(
        model=CustomerModel,
        related_counts=[RelatedCount(OrderModel.customer_id, label="Pedidos")],
    )
)
site.register(AdminModel(model=OrderModel))
```

A listagem ganha a coluna **Pedidos** com a contagem por linha, e cada
número é um link para a listagem de pedidos já filtrada pelo cliente
(`?filter_customer_id=<id>`) — o filtro vale mesmo com `customer_id` fora
do `list_filter` dos pedidos, aparece como um chip **Filtrado por** com
**Limpar filtro**, e o export da listagem filtrada respeita o mesmo
filtro.

A contagem de uma página inteira sai de **uma** consulta,
`SELECT customer_id, COUNT(*) ... WHERE customer_id IN (<ids da página>)
GROUP BY customer_id`, nunca uma por linha — o teste conta os statements
de uma página de 25 linhas e acha um só `GROUP BY`.

- A consulta leva o `row_scope` do admin do filho: o número conta só os
  pedidos que o principal alcança.
- Sem `VIEW` no filho, a coluna some — o número é dado daquele model.
- O link só existe quando o filho tem admin registrado; filho sem admin
  mostra a contagem sem link. Se o admin do filho esconde a coluna FK
  (`exclude_fields`, `__secret_columns__`), também não há link, e o
  `?filter_<fk>=` é ignorado na listagem do filho: filtrar por uma coluna
  que a tela não mostra responderia, pela contagem, o que ela guarda.
- `RelatedCount` recebe o atributo mapeado (para saber o model do filho);
  a coluna precisa ter `ForeignKey` para a tabela do pai, ou o
  `AdminModel` levanta `ValueError` na construção.

## Dashboard: cards de métricas de negócio (`dashboard_cards=`)

O dashboard já mostra CPU/RAM/contadores. Para métricas do **seu
negócio** — pedidos hoje, receita vs semana passada, usuários por plano
— passe `dashboard_cards`. Cada card é um `MetricCard(label, compute)`
onde `compute` é uma função async que recebe a sessão e devolve um de
três tipos:

```python
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    MetricCard,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import OrderModel
from src.db.repositories import OrderRepository


async def orders_today(session: AsyncSession) -> MetricValue:
    total = await OrderRepository(session).count(filters={"start_in": date.today()})
    return MetricValue(total, unit="pedidos")


async def revenue_between(session: AsyncSession, start: date, end: date) -> float:
    """Soma a receita dos pedidos criados em [start, end)."""
    result = await session.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            func.date(OrderModel.created_at) >= start,
            func.date(OrderModel.created_at) < end,
        )
    )
    return float(result.scalar_one())


async def revenue_trend(session: AsyncSession) -> MetricTrend:
    tomorrow = date.today() + timedelta(days=1)
    this_week = tomorrow - timedelta(days=7)
    last_week = this_week - timedelta(days=7)
    return MetricTrend(
        value=await revenue_between(session, this_week, tomorrow),
        previous=await revenue_between(session, last_week, this_week),
        unit="BRL",
    )


async def users_by_plan(session: AsyncSession) -> MetricPartition:
    return MetricPartition(segments=[("free", 120), ("pro", 30), ("enterprise", 4)])


site = AdminSite(
    title="Shop",
    dashboard_cards=[
        MetricCard("Pedidos hoje", orders_today, help_text="desde 00h"),
        MetricCard("Receita", revenue_trend),
        MetricCard("Usuários por plano", users_by_plan),
    ],
)
```

- **`MetricValue(value, unit=None)`** — um número grande.
- **`MetricTrend(value, previous, unit=None)`** — número + seta ▲/▼ e a
  variação percentual vs o período anterior (`delta` / `pct` /
  `direction` calculados pra você).
- **`MetricPartition(segments=[(label, value), ...])`** — breakdown com
  barras proporcionais; `total` somado automaticamente.

Os cards renderizam no topo do dashboard, computados a cada load. Um
card cujo `compute` levanta é **pulado** — uma métrica quebrada nunca
zera a página — e a falha vai para o log com traceback
(`logger.exception` no logger `tempest_fastapi_sdk.admin.router`, nomeando
o `label` do card), então o card que sumiu da tela aparece no log.

## Áreas de indicadores: schema + service (`metrics_areas=`)

O `MetricCard` recebe a sessão e consulta o banco direto, card por card.
Quando o seu service **já sabe** coletar os indicadores, repetir a
consulta no admin é trabalho dobrado — e pula a camada de service. A
`MetricsArea` é a outra forma: você entrega **um schema com um campo por
indicador** e **um service cujo método não recebe argumento**, e o admin
gera uma página com um card por campo, listada em **Metrics** na sidebar.

```python
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminSite, BaseUserModel, MetricsArea, Threshold


class UserModel(BaseUserModel):
    __tablename__ = "users"


class RideMetricsSchema(BaseModel):
    messages_to_route: float = Field(
        title="Mensagens até achar a rota",
        description="Média de mensagens antes do usuário chegar à rota que queria.",
    )
    accesses: int = Field(title="Acessos")
    accounts_created: int = Field(title="Contas criadas")
    rides_created: int = Field(title="Caronas criadas")


class RideMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        accounts = await self.session.scalar(select(func.count()).select_from(UserModel))
        return RideMetricsSchema(
            messages_to_route=4.2,
            accesses=1200,
            accounts_created=accounts or 0,
            rides_created=87,
        )


site = AdminSite(
    title="Transporte",
    metrics_areas=[
        MetricsArea(
            "Caronas",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
            thresholds={
                "messages_to_route": Threshold(green=3, yellow=6, lower_is_better=True),
                "accounts_created": Threshold(green=100, yellow=40),
                "rides_created": Threshold(green=100, yellow=40),
            },
            units={"rides_created": "caronas"},
        )
    ],
)
```

A página fica em `/admin/metrics/caronas` (o slug sai do título). Peça por
peça:

- **`provider`** constrói o service a partir da sessão do admin. A própria
  classe serve quando o `__init__` recebe a sessão; uma função de
  composição (`build_ride_metrics_service`) também. Pode ser sync ou
  `async`.
- **`method`** é chamado **sem argumento** e devolve uma instância do
  `schema`. Outra coisa vira erro na página.
- **Cada campo vira um card**: `title` é o rótulo (o nome do campo quando
  falta), `description` a linha embaixo do valor. Número vira um card de
  valor; um campo tipado `MetricValue`, `MetricTrend` ou `MetricPartition`
  renderiza como esse card.
- **`units`** põe a unidade depois do número.

### Thresholds: verde, amarelo, vermelho

`Threshold(green=, yellow=)` pinta o card de um campo numérico (ou o valor
atual de um `MetricTrend`) com uma borda e um selo — **OK**, **Attention**
ou **Critical**:

| `Threshold(green=100, yellow=40)` (maior é melhor) | Faixa |
| --- | --- |
| `>= 100` | verde |
| `>= 40` e `< 100` | amarelo |
| `< 40` | vermelho |

Com `lower_is_better=True` a comparação inverte — `Threshold(green=3,
yellow=6, lower_is_better=True)` é verde até 3, amarelo até 6 e vermelho
acima. O limite exato cai sempre na faixa melhor. A cor vem dos tokens do
tema (`--tempest-success`, `--tempest-warning`, `--tempest-danger`), então
light e dark seguem consistentes.

!!! tip "Erro cedo, não card sem cor"
    `thresholds` ou `units` com um nome que o schema não tem levanta
    `ValueError` na construção da `MetricsArea`, e limites na ordem errada
    para a direção (`green < yellow` com maior-é-melhor) levantam na
    construção do `Threshold`. Um typo nunca vira um card que simplesmente
    não pinta.

### Quando o service falha

Se o `provider` ou o método levantam, a página responde `200` com um
aviso no lugar dos cards, e o traceback vai para o log
(`logger.exception` em `tempest_fastapi_sdk.admin.router`, nomeando a
área). Um service quebrado nunca vira `500` no painel.

### Quem vê

A área é a superfície `AdminSurfaceEnum.METRICS`: com `surface_policy`,
quem a política nega recebe `403` na página e não vê os links na sidebar.

## Mapa de distribuição geográfica (`location_maps=`) *(v0.367.0+)*

Com o [`BRLocationMixin`](geo.md#cidade-e-estado-do-usuario-brlocationmixin-v03670)
no model, o painel desenha **onde** estão as linhas: um mapa do Brasil com
cada estado num tom conforme a contagem e uma bolha por cidade, a legenda,
as tabelas de estados e cidades e quantas linhas ainda não têm localização.
Registre um `BRLocationMap` por mapa em `AdminSite(location_maps=[...])`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, BRLocationMap
from tempest_fastapi_sdk.geo import BRLocationMixin


class UserModel(BRLocationMixin, BaseUserModel):
    """Users with the city and state they live in."""

    __tablename__ = "users"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./painel.db")
site = AdminSite(
    title="Painel",
    location_maps=[
        BRLocationMap("Usuários por região", UserModel, value_label="usuários"),
        BRLocationMap(
            "Usuários ativos",
            UserModel,
            where=lambda: UserModel.is_active.is_(True),
            value_label="usuários",
        ),
    ],
)
site.register(AdminModel(UserModel))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and a few users spread over three cities."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        admin = UserModel(email="admin@example.com", hashed_password="", is_admin=True)
        admin.set_password("troque-esta-senha")
        session.add(admin)
        places = [("São Paulo", "SP"), ("São Paulo", "SP"), ("Campinas", "SP")]
        places.append(("Recife", "PE"))
        for index, (city, state) in enumerate(places):
            user = UserModel(email=f"user{index}@example.com", hashed_password="")
            user.set_br_location(city, state)
            session.add(user)
        await session.commit()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
        cookie_secure=False,
    )
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Cada mapa vira uma entrada na seção **Maps** da barra lateral, em
`/admin/maps/<slug>` (o slug sai do título, sem acento:
`usuarios-por-regiao`). Medido com o exemplo, por requisição ao painel:

- a barra lateral lista `/admin/maps/usuarios-por-regiao` e
  `/admin/maps/usuarios-ativos`;
- a página responde `200` com Total `5`, With location `4` e No location `1`
  (o administrador não tem cidade);
- as bolhas, maior primeiro: `São Paulo (SP): 2 usuários`,
  `Campinas (SP): 1 usuários`, `Recife (PE): 1 usuários`;
- a dica de cada estado (`SP: 3 usuários`, `PE: 1 usuários`,
  `BA: 0 usuários`) e a legenda com as faixas de cada tom.

O que cada argumento faz:

- `where=` recebe uma função que monta o filtro a cada requisição
  (`lambda: UserModel.is_active.is_(True)`), aplicado a todas as contagens.
- `city_limit=` (padrão 50) limita as bolhas e a tabela às maiores cidades.
- `value_label=` é a palavra depois de cada número nas dicas e nas tabelas.

O mapa é um SVG gerado no servidor, com os contornos dos estados embutidos no
pacote: a página **não faz nenhuma requisição externa** (nem script de mapa,
nem servidor de tiles). As cores saem das variáveis do tema, então o modo
escuro (`AdminTheme(dark_mode=True)`) já vale. Validado em browser real a
1366 px e a 390 px, nos temas claro e escuro: no celular, o mapa ocupa a
largura toda e as tabelas vão para baixo dele.

!!! info "Quem vê"
    A página pede `VIEW` no model à `access_policy`, perguntado ao
    `AdminModel` registrado do model (ou a um `AdminModel(model)` descartável,
    quando ele não está registrado). Negado, a página responde `403` e some
    da barra lateral.

!!! note "Quando a contagem falha"
    Se o `where=` ou o banco levantam, a página renderiza assim mesmo, com o
    aviso `This map could not be counted` no lugar do mapa e o traceback no
    log — como nas áreas de indicadores.

## Import CSV (`can_import=True`)

O admin já exporta a listagem (CSV/JSON). A contraparte: subir um CSV
para **criar** registros em massa. Habilite com `can_import=True`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Product


site.register(AdminModel(model=Product, can_import=True))
```

Aparece um link **Import CSV** na list view → página com upload. O CSV
precisa de uma linha de cabeçalho com os nomes das colunas editáveis; a
página mostra quais são. O import tem **dois passos**:

1. **Prévia.** O upload valida cada linha com as **mesmas regras do
   create** (tipos, obrigatórios, `choices`) e ensaia os inserts numa
   transação que sempre sofre rollback — então a prévia também mostra o
   que só o banco recusaria (valor duplicado numa `unique`) e o que o
   [`row_scope`](#escopo-por-linha-row_scope) não alcança. Nada é
   gravado. A página diz quantas linhas entram, mostra as primeiras 20 e
   lista cada linha recusada com o número (o cabeçalho é a linha 1, como
   na planilha) e o erro de cada campo.
2. **Confirmar.** O botão **Confirm import of N records** reenvia o CSV
   num campo oculto **assinado** com o `secret_key` do router e preso ao
   model e ao operador da prévia: o que entra é exatamente o arquivo que
   foi visto. As linhas rodam de novo, de verdade, numa transação só, e
   o commit é **tudo ou nada** — se a tabela mudou desde a prévia e uma
   linha agora falha, nada entra e a página volta com os erros. Deu
   certo, o painel redireciona para a listagem com `Created N record(s).`

Com erro em **qualquer** linha o botão de confirmar vem desabilitado, e
o `POST` de confirmação recusa do mesmo jeito: nada é importado pela
metade.

!!! info "Limites do passo assinado"
    - O arquivo vai até **512 KiB** (`ADMIN_IMPORT_MAX_BYTES`); maior que
      isso a prévia responde `400` pedindo para dividir o arquivo. Medido
      sobre seis arquivos de 512 KiB (hex aleatório, ASCII imprimível
      aleatório, Latin-1, CJK, emoji, linhas de um caractere), o campo
      assinado ficou em no máximo 571 099 bytes.
    - A prévia vale **1 hora** (`ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS`).
      Depois disso, ou com o campo adulterado, ou assinado para outro
      model ou outro operador, a confirmação responde `400` e não grava.
    - A confirmação gasta um nonce de uso único: recarregar a resposta não
      importa de novo.

!!! warning "Sinais do repositório só na confirmação"
    A prévia insere pela sessão (depois do gancho `_prepare_insert` do
    repositório), sem o `add` do repositório — nenhum handler de
    `PRE_SAVE`/`POST_SAVE` dispara por uma prévia. A confirmação usa
    `repository.add`, com sinais. Como o commit é único no fim, um
    `POST_SAVE` de uma linha já disparou quando uma linha seguinte faz o
    lote voltar; handler com efeito externo (e-mail, fila) deve olhar o
    commit, não o sinal.

!!! info "Opt-in + requer create"
    `can_import` é `False` por padrão e exige `can_create` (importar é
    criar em massa). Colunas de upload de arquivo não entram pelo CSV.

### Exportar e importar `.xlsx`

Com o extra `[spreadsheet]` instalado, a listagem ganha **Export XLSX** ao
lado de CSV e JSON, e o import aceita `.xlsx` além de `.csv`:

```bash
uv add "tempest-fastapi-sdk[admin,spreadsheet]"
```

- **Export.** As mesmas linhas do CSV — busca, filtros, `row_scope` e o
  teto `export_max_rows` —, numa aba, com o cabeçalho congelado. Datetime
  sai como o **mesmo texto ISO 8601 em UTC** do CSV; número e booleano
  continuam número e booleano. Uma célula de texto que começa com `=`
  continua texto (o `SheetWriter` grava como string), então
  `=HYPERLINK(...)` digitado num nome não vira fórmula no arquivo.
  Caractere de controle que o XML não aceita (`\x00`-`\x08`, `\x0b`,
  `\x0c`, `\x0e`-`\x1f`) é removido da célula, em vez de derrubar o
  export com `500`.
- **Import.** O `.xlsx` (pelo nome ou por abrir como ZIP) é lido da
  **primeira aba**, linha 1 como cabeçalho, e vira o texto CSV que o import
  já valida: daí em diante é o mesmo caminho — prévia, `row_scope`, FK,
  confirmação assinada. O que vai assinado é esse CSV, então o teto de
  512 KiB vale para o upload **e** para o CSV que ele vira. Booleano vira
  `true`/`false`, datetime vira ISO 8601 (sem fuso, como a planilha guarda,
  lido no fuso do model como o CSV), e `3.0` vira `3`.
- **Fórmula sem valor calculado.** Planilha gerada por biblioteca
  (openpyxl, pandas) guarda a fórmula mas não o resultado, e a célula seria
  lida vazia. A prévia recusa o arquivo nomeando as células — `A3: formula
  with no calculated value...` (em pt-BR, `fórmula sem valor calculado`);
  abrir e salvar no Excel ou LibreOffice grava o valor. Fórmula que o Excel
  calculou como texto vazio (`=IF(A2="";"";...)`) é válida e entra vazia, e
  só as colunas com cabeçalho na linha 1 são conferidas: fórmula numa coluna
  que o import ignora não recusa o arquivo.
- **Limites da leitura.** A aba é lida com teto de
  `ADMIN_XLSX_IMPORT_MAX_ROWS` linhas (`ADMIN_IMPORT_MAX_BYTES // 16`,
  32 768) e `ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES` descompactados
  (32 × o teto, 16 MiB), proporcionais ao teto do CSV; acima deles a
  prévia responde `400` dizendo os dois limites. Uma planilha de linhas com
  menos de 16 bytes de CSV pode ser recusada mesmo cabendo nos 512 KiB.

!!! info "Sem o extra"
    O openpyxl só é carregado quando um `.xlsx` é pedido. Sem o extra, o
    botão **Export XLSX** não aparece, o `accept` do upload volta a ser só
    CSV, e as rotas respondem com a mensagem que nomeia o pacote: o export
    redireciona para a listagem com
    `Exporting .xlsx needs the spreadsheet extra: pip install "tempest-fastapi-sdk[spreadsheet]"`
    e a prévia do import responde `400` com o equivalente.

## Duplicar registro (`can_duplicate=True`)

Para criar um registro parecido com um que já existe, `can_duplicate=True`
põe **Duplicar** no detail:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import ProductModel


site.register(AdminModel(model=ProductModel, can_duplicate=True))
```

O botão abre o form de **criação** preenchido com os valores do registro,
menos o que uma cópia não pode carregar: a identidade, as colunas únicas
(`unique=True`, `UniqueConstraint` e índice único — composto inclusive),
senha, upload, colunas secretas e as de auditoria (`created_at`,
`updated_at`, `created_by`, `updated_by`). Os vínculos de
[`m2m_fields`](#muitos-para-muitos-no-form-m2m_fields) que o principal
alcança vêm marcados.

O form posta para o create comum, então a cópia passa por **toda**
checagem de um registro novo — validação, FK no escopo, `row_scope`
depois da escrita. Exige `CREATE` (e `VIEW`, porque lê a origem), e a
origem precisa estar no escopo: fora dele, `404`. Precisa do form de
colunas: com `can_create=False` ou com `create_handler`, a construção
levanta `ValueError`.

## Edição em massa de um campo (`bulk_edit_fields=`)

Arquivar 40 pedidos de uma vez sem abrir um por um:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        bulk_edit_fields=[OrderModel.status, OrderModel.assignee_id],
        choices={OrderModel.status: ["open", "shipped", "archived"]},
    )
)
```

O menu de ações da seleção ganha **Editar campo**. Marque as linhas,
escolha a ação, escolha o campo (passo pulado quando só há um) e digite o
valor no **widget do próprio campo** — o `<select>` de `choices`, o de
FK. O valor é validado como no form de edição: tipo,
obrigatório, `choices`, a FK no escopo do alvo, o `validate_edit` de cada
linha, e um campo que o `readonly_fields_for` tranca em qualquer linha
selecionada recusa a edição inteira.

A escrita é **um** `UPDATE ... WHERE id IN (...) AND <escopo>` numa
transação:

- só os ids selecionados que o principal alcança entram;
- se o valor novo tira alguma linha do escopo, a transação volta inteira e
  a listagem mostra o aviso de escopo;
- com `audit_model`, cada linha ganha a sua entrada `update` com o diff,
  na mesma transação;
- `updated_by` recebe o operador e `updated_at` anda — um form de edição
  aberto antes acusa o [conflito](#edicao-simultanea-trava-otimista).

Exige `can_edit` e `EDIT`. O passo do valor leva um nonce de uso único:
recarregar a resposta não aplica de novo.

!!! warning "Sem sinais do repositório"
    Como o activate/deactivate em lote, a edição em massa é SQL Core:
    handlers de `PRE_SAVE`/`POST_SAVE` não disparam. Cada nome em
    `bulk_edit_fields` precisa ser coluna editável do form (nem upload, nem
    senha, nem `readonly_fields`), conferido na construção.

## RBAC granular (`access_policy=`)

Por padrão, quem loga no admin (`is_admin`) faz tudo o que os flags
`can_*` de cada `AdminModel` permitem. Para dar a um admin acesso só a
**alguns** modelos/ações — um "suporte" que vê pedidos mas não apaga,
um "editor" que só mexe em conteúdo — passe um `access_policy` no
`make_admin_router`:

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
        return action is AdminPermissionEnum.VIEW      # só leitura
    return admin.model is not AuditLog             # editor: tudo menos AuditLog


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

A política é consultada `(principal, admin, action)` em **toda** ação —
`VIEW` / `CREATE` / `EDIT` / `DELETE`. Negar dá `403`; negar `VIEW`
também **esconde** o modelo do dashboard e da barra lateral, e negar
create/edit/delete some com os botões correspondentes. Vale em list,
detail, create, edit, delete, bulk (delete → `DELETE`, resto → `EDIT`),
export, import e autocomplete de FK.

Vale também para o que a página de um model mostra de **outro** model:

- a tabela de inline no detail do pai só aparece com `VIEW` no filho — um
  filho sem `AdminModel` registrado é perguntado por um
  `AdminModel(Filho)` descartável, então uma policy que libera por
  `admin.model` decide igual;
- no inline editável, a linha de adicionar exige `CREATE` (e o
  `can_create` do filho) e a caixa de apagar exige `DELETE`; um `POST` do
  formset que peça a ação negada responde `403` sem gravar nada;
- o `<select>` de FK, o filtro de FK da listagem e o autocomplete exigem
  `VIEW` no model referenciado. Sem ele, o campo de FK vira input de texto
  e o autocomplete responde `403`.

!!! info "Compõe com os flags `can_*`"
    A policy **soma** aos flags `can_create`/`can_edit`/`can_delete` do
    `AdminModel` — os dois precisam liberar. Sem `access_policy`, nada
    muda (todo admin logado faz tudo). Pode ser sync ou `async`.

### Uma ação sem `EDIT` no model (`action_policy=`) *(v0.366.0+)*

Sem mais nada, toda ação customizada pede `EDIT` no model à `access_policy`.
Num painel só de leitura, liberar **uma** ação ("Confirmar e-mail") obrigava
a liberar `EDIT` no model inteiro — e, se alguém ligasse o `can_edit` por
engano, a mesma permissão abria a edição livre de qualquer campo. Também não
dava para dar a um papel uma ação sem dar outra.

Passe um `action_policy`: ele é perguntado `(principal, admin, AdminAction)`
para cada ação customizada, e a ação passa a pedir `VIEW` no model mais a
aprovação dele, não `EDIT`:

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
    """Painel só de leitura: VIEW em todo model, nada mais."""
    return action is AdminPermissionEnum.VIEW


def action_policy(user: UserModel, admin: AdminModel, action: AdminAction) -> bool:
    """Libera só a ação de confirmar e-mail, só para o suporte."""
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

Medido em `tests/admin/test_custom_action_flow.py`:

- a ação liberada roda (`303`) e a negada responde `403`, no mesmo model;
- o menu da lista só mostra as ações que a política libera — e, de quebra,
  Activate / Deactivate e Delete só aparecem quando o model os habilita
  (`can_edit` + `can_toggle_active`, `can_delete`) **e** o principal tem
  `EDIT` / `DELETE`; sem nenhuma ação permitida, o formulário de lote some;
- com `EDIT` negado, o formulário de edição continua `403`, mesmo com a ação
  liberada.

Sem `action_policy`, nada muda: a ação customizada pede `EDIT`, como antes.

### Console SQL, tasks e logs (`surface_policy=`)

O console SQL, o painel de tasks, a página de logs e o navegador de storage
não são de um model, então a `access_policy` não tem o que perguntar. Quem decide é a
`surface_policy`, consultada `(principal, AdminSurfaceEnum)`:

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

Negar responde `403` em todas as rotas da superfície (o `POST` do
console, o cancelamento de task, o export de logs e as escritas e o `raw`
do storage incluídos) e tira a
entrada da barra lateral.

!!! warning "Com `access_policy`, as superfícies fecham por padrão"
    Sem nenhuma das duas policies, todo admin abre todas as superfícies,
    como antes. Com `access_policy` e **sem** `surface_policy`, ninguém
    abre: o console lê qualquer tabela que a `SqlShellPolicy` libera, e um
    operador restrito a alguns models leria o resto por ali. Passe uma
    `surface_policy` para devolver as superfícies a quem deve tê-las.

## Escopo por linha (`row_scope=`)

O `access_policy` responde sim ou não para o **model inteiro**. No
multi-tenant isso não basta: o suporte da empresa A pode ver pedidos,
mas só os da empresa A. Contornar com um painel por empresa ou um
repositório filtrado à mão é onde o dado vaza — basta uma rota esquecer
o filtro. `row_scope=` põe o filtro no painel:

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

A função recebe o principal logado e o próprio `AdminModel`, e devolve
uma condição SQLAlchemy. "Todas as linhas" se diz explicitamente, com
`sqlalchemy.true()`. Pode ser `async` (para buscar o tenant em outra
tabela, por exemplo). A condição entra com `AND` em **toda** rota que lê
ou escreve linha do model:

- listagem, contagem (no cabeçalho e no card do dashboard) e export;
- detail, form de edição e o `POST` dele, delete e o download de upload;
- ações em lote — delete, activate/deactivate — e ações customizadas: os
  ids selecionados fora do escopo são descartados como se não existissem;
- autocomplete, `<select>` de FK e filtro de FK **quando este model é o
  alvo**, e a tabela de inline quando ele é o **filho**;
- o formset do inline editável (o pai e cada filho são lidos no escopo);
- a prévia e a confirmação do import CSV e `.xlsx`, e o export `.xlsx`;
- o duplicar (a origem é lida no escopo), a edição em massa, as opções e
  os vínculos de `m2m_fields` e a contagem de `related_counts` (com o
  escopo do model alvo ou filho), e o feed de atividade recente.

Linha fora do escopo responde `404` — o mesmo de uma linha que não
existe, para a resposta não confirmar que ela está lá. No delete e nas
ações em lote embutidas, a condição está no próprio `DELETE` / `UPDATE`,
não só na leitura que veio antes.

!!! danger "Devolver `None` não libera tudo"
    Uma função de escopo que esquece o `return` devolve `None`. O painel
    trata isso como erro: levanta `AdminRowScopeError`, registra no log o
    nome do model e a requisição responde `500` — fecha, em vez de abrir
    todas as linhas. Para liberar tudo, devolva `true()`.

    E cuidado com o principal sem tenant: `TicketModel.company_id ==
    user.company_id` com `company_id` `None` vira `IS NULL`, e casa com
    toda linha sem empresa. Se esse usuário não deve ver nada, devolva
    `false()` antes, como no exemplo.

### Na escrita

A regra vale para o resultado: uma linha **criada**, **editada** ou
**importada** precisa continuar dentro do escopo depois da escrita. O
painel grava dentro de uma transação, conta a linha contra a condição na
mesma transação e, se ela saiu, faz rollback e devolve o form com
`This record would fall outside the rows you may manage; nothing was
saved.` — o operador da empresa A não cria um ticket da empresa B nem
"move" um dos seus para lá trocando o `company_id`. O mesmo vale para:

- **`create_handler`**: os campos do schema com o nome de uma coluna FK
  passam pela checagem de FK abaixo antes do handler rodar. Depois, toda
  linha do model que o handler grava pela sessão — inserida **ou
  alterada**, como num upsert que edita uma linha existente — é contada
  contra o escopo antes do commit; fora, rollback. Escapam da checagem:
  escrita por SQL Core (`insert()`, `update()`, `bulk_create_values`), um
  service que abre uma sessão própria e linhas de **outros** models que o
  handler grave. Handler que dá `commit` sozinho já tornou a linha
  durável: o painel não consegue desfazer, registra em `ERROR` com o model
  e diz no form que o registro **foi** salvo fora do escopo, em vez de
  afirmar que nada foi salvo.
- **activate/deactivate em lote**: se o `is_active` novo tira alguma
  linha do escopo (um escopo que filtra por ele), nada muda e a listagem
  volta com o aviso.
- **edição em massa** (`bulk_edit_fields=`): a mesma regra — o `UPDATE`
  leva o escopo no `WHERE`, e um valor que tira qualquer linha do escopo
  volta a transação inteira.
- **muitos-para-muitos** (`m2m_fields=`): cada id postado precisa estar no
  escopo do alvo, e o vínculo que o operador não alcança é mantido.
- **chave estrangeira**: num create, edit, inline ou import, o valor de
  uma FK cujo model alvo tem `row_scope` precisa ser uma linha que o
  principal alcança lá. Fora do escopo, o campo recebe `Select a valid
  choice; that record does not exist.` — o mesmo erro de um id que não
  existe, então um `POST` forjado não descobre ids de outro tenant. A
  coluna é conferida contra a sua primeira `ForeignKey`: numa
  `ForeignKeyConstraint` composta, ou numa coluna com várias FKs, só esse
  alvo é verificado.

Um arquivo de upload gravado por um create ou edit que acabou recusado é
apagado (tentativa única; falha vai para o log).

No form de edição, uma FK que aponta para uma linha que o operador **não**
alcança (ou além do `FK_OPTION_CAP`) continua selecionada: o `<select>`
ganha uma opção com o valor atual, rotulada só com o id, e salvar sem
mexer nela mantém a referência — antes o browser mandava a primeira
opção e o save trocava a referência sem aviso.

No painel de auditoria do detail, o autor de uma alteração aparece pelo
nome quando o model de usuário não está registrado no site, ou está sem
`row_scope`. Com `row_scope`, o nome só aparece se o principal tem `VIEW`
nesse model e alcança aquele usuário pelo escopo; senão, só o id.

No delete, quando o `DELETE ... AND <escopo>` não apaga nada (a linha
saiu do escopo depois da leitura), o bloco volta inteiro — o que um
handler de `PRE_DELETE` gravou na sessão é desfeito —, a resposta é `404`
e o `POST_DELETE` não dispara. O `POST_DELETE` recebe a instância fora da
sessão, como no `BaseRepository.delete`, então lê as colunas mesmo com
`expire_on_commit=True`.

!!! warning "O que o escopo não cobre"
    - O `row_scope` vale nas rotas `/m/{slug}/...` e no que elas mostram de
      outro model. Ficam de fora o que roda código seu ou SQL livre: os
      cards do dashboard (`dashboard_cards=`), as áreas de indicadores, os
      mapas (`location_maps=`) e o console SQL.
    - Numa ação customizada, os `ctx.ids` já vêm filtrados, mas o
      `ctx.repository` é o repositório do model sem filtro, e o painel não
      confere o resultado: a ação pode ler além dos ids e pode **mover**
      uma linha para fora do escopo. Isso é responsabilidade dela.
    - O navegador de storage (`/storage`, superfície `STORAGE`) serve
      qualquer chave do bucket. Se os uploads do model escopado moram no
      mesmo bucket, quem tem a superfície contorna o escopo do download de
      upload — negue `STORAGE` a esses papéis no `surface_policy`.
    - Create e import continuam sendo oráculo de `UNIQUE` entre tenants:
      um código que já existe na empresa B dá conflito para a empresa A.
      A saída é a constraint `UNIQUE` composta com a coluna do tenant
      (`UniqueConstraint("company_id", "code")`).

!!! tip "Junto com o `access_policy`"
    Os dois compõem: o `access_policy` decide **se** o principal abre o
    model e qual ação faz; o `row_scope` decide **quais linhas**. Um
    suporte com `VIEW` e um escopo por empresa vê a listagem só da própria
    empresa e recebe `404` no detail de qualquer outra.

## Widgets de campo por tipo de coluna

O form de create/edit escolhe o widget pelo tipo da coluna, sem
configuração: `bool` → checkbox, `Enum` → select, `int`/`float`/`Decimal`
→ number, `datetime`/`date`/**`time`** → inputs nativos, string longa →
textarea, **coluna `JSON`** → editor JSON monoespaçado (pretty-print ao
abrir, `json.loads` + validação no submit — JSON inválido vira erro de
campo, não uma string salva no lugar). FK vira `<select>` (ou
autocomplete, via `autocomplete_fields`), e `upload_fields` vira input de
arquivo.

## Senha no form (`password_fields=`)

Um modelo que herda `BaseUserModel` **não podia ser criado pelo painel**.
O form não mostra `hashed_password` (ninguém digita um digest bcrypt num
`<input type="text">`), a coluna é `NOT NULL`, e o insert respondia um 400
com `Conflict creating <Model>` — mensagem que o operador lê como "e-mail
já cadastrado".

`password_fields=` fecha o caminho: a caixa aceita texto puro e o save
grava o hash.

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite, PasswordPolicy

from src.db.models import LocalUserModel

site = AdminSite(title="Painel")
site.register(
    AdminModel(
        LocalUserModel,
        password_fields=[LocalUserModel.hashed_password],
        password_policy=PasswordPolicy(min_length=12, require_complexity=True),
        list_display=[LocalUserModel.name, LocalUserModel.email],
    ),
)
```

O que muda, campo a campo:

- **Create:** `<input type="password" autocomplete="new-password">`,
  obrigatório enquanto a coluna for `NOT NULL`. O valor passa por
  `check_password_policy` e, reprovando, vira erro **no campo** — não mais
  um `ConflictException`. O rótulo perde o prefixo `hashed_`
  (`hashed_password` → **Password**): a caixa recebe texto puro, e chamá-la
  de "Hashed Password" descreveria a coluna, não o que se digita.
- **Edit:** a caixa nasce vazia e vazio significa *não mexer*. Preenchida,
  rotaciona o hash. O digest **nunca** pré-popula o input.
- **Hash:** usa o `set_password` do próprio modelo quando existe (é o que
  `BaseUserModel` ships, e onde um projeto troca o algoritmo); senão,
  `PasswordUtils`.
- **Nunca aparece:** fora do `list_display` (mesmo declarado à mão), fora
  do detail e fora do export CSV/JSON.

!!! warning "`password_fields` não combina com `can_import=True`"
    Hashear uma coluna de texto puro vinda de arquivo é outra decisão, e o
    importador postaria linha nenhuma com senha. A combinação levanta
    `ValueError` na construção do `AdminModel` — no boot, não em produção.

!!! tip "A política vem das suas settings"
    `PasswordPolicy.from_settings(settings)` lê `AUTH_PASSWORD_MIN_LENGTH`,
    `AUTH_PASSWORD_MAX_BYTES` e `AUTH_PASSWORD_REQUIRE_COMPLEXITY` do
    `Settings` que você já compõe de `AuthSettings` — assim o painel aplica
    a mesma regra que o `signup`. Sem argumento, valem os defaults do
    `AuthSettings`.

## Coluna que nunca aparece (`exclude_fields=`)

Nem toda credencial é senha. A assinatura de Web Push guarda `endpoint`,
`p256dh` e `auth` — quem tem os três manda push para aquele aparelho —, o
usuário guarda um `push_token` e um `totp_secret`. `readonly_fields` não
resolve: ele tranca o input e **mostra** o valor no detail, para qualquer
admin.

`exclude_fields=` tira a coluna do painel inteiro:

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import LocalUserModel, WebPushSubscriptionModel

site = AdminSite(title="Painel")
site.register(
    AdminModel(
        LocalUserModel,
        exclude_fields=[LocalUserModel.totp_secret, LocalUserModel.push_token],
    ),
)
site.register(
    AdminModel(
        WebPushSubscriptionModel,
        exclude_fields=[
            WebPushSubscriptionModel.endpoint,
            WebPushSubscriptionModel.p256dh,
            WebPushSubscriptionModel.auth,
        ],
        can_create=False,
    ),
)
```

A coluna sai de **toda** superfície de uma vez: listagem, detail, form de
create e edit (um `POST` que mande o campo à mão é ignorado), import CSV,
export CSV/JSON, cabeçalho ordenável (`?sort=endpoint` é ignorado e vale a ordenação
default), a tabela de inline no detail do pai — mesmo que o `Inline`
declare a coluna no próprio `list_display` —, as linhas de antes/depois da
linha do tempo de auditoria, e o rótulo que o `<select>` de FK de outro
model mostra. Todas leem o mesmo conjunto, `hidden_field_names()`, que é
`hashed_password` + `__secret_columns__` do model + `password_fields` +
`exclude_fields`.

!!! info "As credenciais dos models do SDK já saem sozinhas"
    `MFAMixin.totp_secret`, `endpoint`/`p256dh`/`auth` do
    `BaseWebPushSubscriptionModel`, `token`/`p256dh`/`auth` do
    `BaseDeviceTokenModel`, `token_hash` do `BaseUserRefreshTokenModel` e
    `code_hash` do `BaseUserRecoveryCodeModel` são declarados em
    `__secret_columns__`. Um `AdminModel(model=...)` simples — ou o
    `automap` — já os esconde de toda superfície acima, e a auditoria os
    grava como `[redacted]`, sem `exclude_fields` nem `__audit_redact__`.
    O `exclude_fields` continua sendo o caminho para a coluna do **seu**
    model, como o `push_token` acima. A coluna secreta `NOT NULL` sem
    default ainda aparece em branco no form de **create** (nunca no de
    edit), para a assinatura de push continuar podendo ser criada pelo
    painel. Um model seu declara as próprias com
    `__secret_columns__: ClassVar[frozenset[str]] = frozenset({"api_key"})`.

A configuração que se contradiz falha na construção do `AdminModel`, no
boot, em vez de renderizar o segredo:

- nome que não é coluna do model;
- coluna excluída que outra opção também nomeia: `list_display`,
  `list_filter`, `search_fields`, `readonly_fields`, `upload_fields`,
  `autocomplete_fields`, `password_fields`, `identity_field`, `ordering` ou
  um `Lens` (`filters` ou `order_by`);
- coluna `NOT NULL` sem default excluída com `can_create=True` — o form
  nunca a preencheria, e todo create acabaria em `Conflict creating
  <Model>`. É o caso da assinatura de push acima, por isso o
  `can_create=False`.

### E a tabela de auditoria?

Esconder a linha do tempo não basta: com `audit_model=`, o `add_audited` /
`update_audited` gravariam o valor na própria tabela de auditoria, legível
por query ou pelo console SQL do painel. Por isso o `AdminModel` com
`audit_model=` **recusa** uma coluna excluída que o model não declare em
`__audit_redact__`:

```python
# src/db/models.py
from typing import ClassVar

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class WebPushSubscriptionModel(BaseModel):
    """Assinatura de Web Push de um aparelho."""

    __audit_redact__: ClassVar[frozenset[str]] = frozenset(
        {"endpoint", "p256dh", "auth"}
    )

    platform: Mapped[str] = mapped_column(String(16))
    endpoint: Mapped[str] = mapped_column(String(512))
    p256dh: Mapped[str] = mapped_column(String(128))
    auth: Mapped[str] = mapped_column(String(64))
```

A entrada de auditoria grava `"[redacted]"` no lugar do valor. No update, o
diff é calculado sobre os valores reais antes da troca, então rotacionar a
chave ainda aparece — `{"before": "[redacted]", "after": "[redacted]"}` —, e
`None` continua `None`, para "não definido" seguir distinguível de
"definido". `hashed_password` é redigido sempre, declarado ou não.

!!! note "`can_import=True` continua permitido"
    Ao contrário de `password_fields`, não há decisão escondida no import: a
    coluna excluída simplesmente não é importável, como não é editável.

## Fuso do operador (`display_timezone=`)

Uma coluna `TIMESTAMP(timezone=True)` guarda **instante**, e o form do
admin renderiza `<input type="datetime-local">`, que **não carrega
offset nenhum**. Sem dizer em que fuso a caixa é lida, o painel vira um
editor de coluna crua: a submissão chega sem `tzinfo`, e quem decide o
que ela significa passa a ser o driver — com asyncpg, o `TZ` do
processo. Num container em UTC o operador em Teresina digita `20:00` e
a linha fica com `20:00Z`, três horas à frente
([medido no consumidor](https://github.com/And-All/alofans-api/issues/114)).

Declare o fuso e as três telas passam a concordar:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import EventModel


site.register(
    AdminModel(
        model=EventModel,
        list_display=[EventModel.name, EventModel.date],
        display_timezone="America/Sao_Paulo",
    ),
)
```

O que muda, e só isso:

- **Listagem e detail** convertem cada `datetime` para o fuso e mostram
  o offset junto (`2026-06-15 20:00 -03:00`; o instante completo fica no
  tooltip), então a tela nunca é ambígua.
- **Form** pré-preenche a caixa com a hora local (`2026-06-15T20:00`) e
  escreve `Timezone: America/Sao_Paulo — stored as UTC.` embaixo, porque
  o `datetime-local` não tem onde dizer isso sozinho.
- **Submit** lê o valor como hora de parede **naquele** fuso e grava em
  UTC. Valor que já chega com offset é respeitado, não sobrescrito.
- **Filtro de intervalo de data** (`list_filter` numa coluna `datetime`)
  lê o dia nesse fuso: "de 20/11 até 20/11" vai da meia-noite de 20/11
  até a meia-noite de 21/11 em São Paulo, fim exclusivo. Um registro das
  22:00 de 20/11 (01:00 UTC de 21/11) entra no dia 20, que é o dia que a
  listagem mostra. O export filtrado segue o mesmo intervalo. Sem
  `display_timezone`, o dia é UTC.

!!! warning "Converter só na escrita é pior que não converter"
    A meia-correção passa em todo teste sobre gravar e ainda assim
    move a linha: o form mostra a hora UTC, o operador salva sem tocar
    em nada, e o valor exibido é relido como hora local. Cada save de
    um formulário intocado empurra a linha três horas. É por isso que
    as duas pontas mudam juntas, e por isso o teste que fixa a regra é
    um `POST` real de um form que ninguém editou.

!!! tip "Declare uma vez no `AdminSite`"
    O fuso é do painel, não de uma tabela: `AdminSite(display_timezone=...)`
    vale para todo model registrado depois, e um `AdminModel` que declara o
    seu próprio continua com ele.

!!! info "O export continua em UTC"
    CSV e JSON alimentam máquina. Um timestamp que muda de significado
    entre a tela e o arquivo é pior que um que sempre significa a mesma
    coisa, então a conversão fica nas telas que uma pessoa lê.

!!! tip "O fuso é validado onde é declarado"
    `display_timezone="America/Sao_Paolo"` (com o typo) levanta
    `ValueError` na construção do `AdminModel`, não no request que
    renderiza o form. Fuso resolvido tarde já custou caro aqui: a
    string de offset do cron morria dentro do loop do scheduler,
    calada.

### Atalhos no filtro de data

Todo filtro de intervalo de data (`list_filter` numa coluna `date` ou
`datetime`) ganha, embaixo das duas caixas, os links **Today**, **7 days**,
**30 days**, **This month** e **Last month** (em português com
`AdminSite(locale="pt-BR")`):

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        list_filter=[OrderModel.created_at, OrderModel.status],
        search_fields=[OrderModel.code],
        display_timezone="America/Sao_Paulo",
    ),
)
```

Cada link preenche `filter_created_at_from` e `filter_created_at_to` com
os dias do intervalo, ambos inclusivos: hoje; os últimos 7 e 30 dias
terminando hoje; o mês corrente do dia 1 ao último dia; o mês anterior
inteiro. "Hoje" é o dia no `display_timezone` do model (UTC sem ele) —
o mesmo dia que o filtro lê. O resto da query fica como o operador deixou
(busca, os outros filtros, a ordenação, a lens); só a página volta para a
primeira. O atalho que bate com o intervalo ativo aparece marcado. São
links montados no servidor, sem JavaScript.

## Edição simultânea: trava otimista

Dois operadores abrem o mesmo registro; o primeiro salva, o segundo
salva por cima sem saber. O form de edição evita isso sozinho, para
todo model com coluna `updated_at` — e todo `BaseModel` tem uma:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import CustomerModel


site.register(AdminModel(model=CustomerModel))
```

O form leva, num campo oculto, o `updated_at` que leu ao abrir. No save,
antes de atribuir qualquer coluna, o painel roda
`UPDATE ... SET updated_at = updated_at WHERE id = :id AND updated_at =
:lido` na **mesma transação** do `UPDATE` que grava a edição — não há
`SELECT` antes para perguntar se mudou. Zero linhas afetadas significa
que outro save (pelo painel ou por qualquer outro caminho que passe pelo
ORM) chegou antes: nada é gravado e o form volta com `409`, o aviso
`Someone else saved this record after you opened it. Nothing was saved:
your values are below — review them and save again to overwrite.` e os
valores que o operador digitou.

O form devolvido já carrega a versão **atual** da linha. Salvar de novo,
depois de revisar, sobrescreve conscientemente.

!!! info "Onde a trava não vale"
    - Model sem coluna `updated_at`, ou um `POST` sem o campo oculto (um
      form aberto antes da atualização do SDK), salva como antes.
    - O formset do inline editável e as ações em lote/customizadas não
      levam a trava: o inline salva cada filho como está, e uma ação age
      sobre a linha que encontra.

## Interface em português (`locale=`)

Todo texto que o próprio painel escreve — rótulos, botões, estados
vazios, a paginação, os avisos de flash e os erros que o router mostra
no form (`This field is required.`, o aviso de conflito, o de escopo) —
sai de um catálogo com inglês e português do Brasil. Escolha no site:

```python
from tempest_fastapi_sdk import AdminSite

site = AdminSite(
    title="Backoffice",
    locale="pt-BR",
    display_timezone="America/Sao_Paulo",
)
```

O padrão é `"en"`, com o mesmo texto de sempre, então um painel que já
existe não muda. Um `locale` fora de `ADMIN_LOCALES` levanta `ValueError`
na construção do `AdminSite`. O `<html lang>` acompanha o locale, e o
`index_subtitle` padrão vira `Administração do site` (passado à mão, ele
fica como você escreveu).

O que **não** é traduzido é o que vem de você: nome de model e de campo,
`verbose_name`, rótulo de ação, `title`. As mensagens que outro
componente escreve também saem como ele as escreve — o
`AdminAuthError` do backend de auth (`Invalid credentials`), os erros do
`StorageBrowserService` e a mensagem da política de senha.

Para escrever um texto seu no mesmo catálogo — o retorno de uma ação
customizada, por exemplo — use `admin_text`:

```python
from tempest_fastapi_sdk.admin import admin_plural, admin_text

title = admin_text("pt-BR", "list.new", model="Pedido")
count = admin_plural("pt-BR", "list.records", 3)
```

`admin_text(locale, chave, **params)` formata o texto com `str.format`;
`admin_plural(locale, chave, n)` lê `chave.one` com `n == 1` e
`chave.other` no resto. Chave que falta no locale cai no inglês, e chave
desconhecida devolve a própria chave — o guard
`tests/test_admin_i18n_guard.py` mantém as duas línguas completas.

## Lenses — visões salvas (`lenses=`)

Uma lens é um preset nomeado de filtros + ordenação, mostrado como aba
acima da listagem. Em vez de o operador reentrar "status=aberto,
prioridade>=3, mais antigos primeiro" toda vez, ele clica na aba:

```python
from tempest_fastapi_sdk import AdminModel, Lens

from src.admin import site
from src.db.models import Ticket


site.register(
    AdminModel(
        model=Ticket,
        lenses=[
            Lens("Abertos", filters={"status": "open"}),
            Lens(
                "Urgentes",
                filters={"status": "open", "priority__gte": 3},
                order_by="-created_at",
            ),
        ],
    )
)
```

A lista ganha as abas **All / Abertos / Urgentes**. Clicar aplica os
filtros da lens (ANDeados com a busca/filtros que o usuário já usar) e a
ordenação (`order_by`, `-col` = desc, a menos que o usuário clique num
cabeçalho). A lens ativa é preservada em paginação, sort e export; "All"
volta ao padrão.

!!! info "Mesmas convenções de filtro"
    `filters` usa o mesmo dict do repository (`campo__gte`, `name` ILIKE,
    iterável → `IN`, …). O slug da aba (`?lens=`) sai do nome
    (minúsculo, hífens).

#### 4. Defaults de segurança de sessão

`SignedCookieSessionStore` usa `itsdangerous.TimestampSigner` (HMAC-SHA256) para assinar um único cookie:

- `HttpOnly` sempre definido.
- `Secure` marcado quando `cookie_secure=True` (padrão; desligue no dev HTTP local).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` aceitos).
- Tempo de vida padrão `8h`; cookies expirados ou adulterados são rejeitados silenciosamente.
- O cookie não basta: toda rota autenticada relê o principal pelo `auth_backend` a cada request. Conta apagada, desativada ou sem `is_admin` depois do login é mandada para `/admin/login` (`303`) já no request seguinte, sem esperar o cookie expirar.
- O `POST /admin/logout` encerra a sessão no servidor, não só no navegador: ele marca o id aleatório da sessão (`sid`, no cookie) num `AdminSessionRevocations`, e todo request recusa uma sessão marcada. Uma cópia do cookie tirada antes do logout é mandada para `/admin/login`. O default guarda as marcas num `InMemoryThrottleBackend`, por processo, pelo `max_age_seconds` do store. Atrás de mais de um worker ou réplica, passe `make_admin_router(..., session_revocations=AdminSessionRevocations(redis_client))` (o mesmo cliente `redis.asyncio` que o throttle aceita), senão o logout servido por um processo não chega aos outros.
- A sessão fica presa à credencial: no login o router grava no cookie um HMAC (chaveado com o `secret_key`) do `AdminAuthBackend.credential_fingerprint(principal)` e confere a cada request. No `UserModelAuthBackend` a impressão cobre o `hashed_password` e o `totp_secret`, então trocar a senha ou re-cadastrar o MFA encerra toda sessão emitida antes, em todas as réplicas, sem estado extra. Backend próprio devolve `None` por padrão (sem vínculo); sobrescreva o método para ligar. Cookies emitidos antes dessa versão não carregam o HMAC e são recusados uma vez: o operador loga de novo.
- Um token CSRF por sessão é gerado no login e exigido por todo POST de formulário da sessão (logout, criar, editar, excluir, ações em massa, execução no console SQL, cancelamento de task).
- O formulário de login roda antes de existir sessão e carrega um token próprio: o `GET /admin/login` grava um cookie assinado de 1 h (`tempest_admin_login_csrf`, com salt distinto do da sessão) e repete o token num campo hidden. O `POST` compara os dois em tempo constante **antes** do orçamento de falhas e da senha, e recusa com `403` — a página volta com um token novo, e a recusa não gasta tentativa. Sem isso, uma página de outro site faria o navegador do operador logar na conta **do atacante** (login CSRF); `SameSite=Lax` não impede, porque restringe o cookie que o POST envia, não o que a resposta grava. O limite: o cookie fica restrito ao prefixo do admin, então não pode usar o prefixo `__Host-`, e um **subdomínio irmão** sob controle de terceiros (`evil.example.com` ao lado de `admin.example.com`) consegue plantar para o domínio pai um cookie assinado que ele mesmo obteve abrindo o formulário. Sirva o admin num host sem subdomínios de terceiros.
- `secret_key` deve ter ao menos 32 bytes — chaves curtas levantam `ValueError` no momento da construção.

!!! danger "Login recusado ou em loop? É o `Secure` do cookie sobre HTTP puro"
    Se todo `POST /admin/login` responde `403` com *The sign-in form expired,
    please try again*, o cookie do token do formulário **não está voltando**. Se
    responde `303` (parece sucesso), mas o `GET /admin/`
    seguinte redireciona de volta pro login — repetindo pra sempre — o cookie de
    sessão é que não volta. Nos dois casos a causa quase certa é a mesma:
    `cookie_secure=True` enquanto o admin é servido por **HTTP puro** (sem TLS na
    frente). O browser recusa gravar um cookie `Secure` em conexão não-HTTPS, e os
    dois cookies do admin (o do token de login e o da sessão) seguem o
    `cookie_secure`.

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

!!! warning "O browser diz que o formulário de login não é seguro? É o scheme atrás do proxy"
    Com HTTPS na frente e o proxy falando HTTP com a app, o `action` do
    formulário sai `http://...` e o browser marca o campo de senha como não
    seguro. Declare `SERVER_PUBLIC_SCHEME=https` e adicione o
    `PublicSchemeMiddleware` — receita em
    [Segurança](security.md#scheme-publico-atras-de-proxy-server_public_scheme).
    Enquanto falta, o `GET /admin/login` loga um `WARNING` dizendo isso.

##### Logar em teste

Como o `POST /admin/login` exige o token do formulário, teste que posta só as credenciais leva `403`. `tempest_fastapi_sdk.testing` faz o que o navegador faz — abre o formulário, lê o hidden (o cookie fica no jar do cliente) e envia:

```python
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk.testing import admin_login


async def test_dashboard_after_login(app: FastAPI) -> None:
    """Loga pelo formulário, como o navegador faz, e abre o painel."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await admin_login(client, "root@example.com", "hunter2")
        assert response.status_code == 303
        assert (await client.get("/admin/")).status_code == 200
```

Com `TestClient`, use `admin_login_sync(client, ...)`. Os dois aceitam `httpx` e `httpx2` (de que o `TestClient` do Starlette herda quando o `httpx2` está instalado) sem `cast`, e devolvem a resposta no tipo do cliente que você passou. Os dois recebem `prefix=` quando o admin não está em `/admin`, e `headers=` para, por exemplo, o header de IP confiável.

!!! tip "`https://` na `base_url`"
    Com `cookie_secure=True` (o padrão), os cookies do admin saem `Secure`, e o cliente HTTP do teste só os devolve sobre `https://`. Use `base_url="https://test"`, ou monte o router com `cookie_secure=False` no teste.

##### Orçamento de falhas no login

`POST /admin/login` conta tentativas erradas em dois orçamentos. O do
identificador vem ligado por padrão — todo admin já montado ganha a
proteção sem configurar nada —, e o do IP liga sozinho assim que você diz
qual header carrega o IP do cliente:

| Parâmetro | Chave | Padrão | Zera quando |
| --- | --- | --- | --- |
| `login_throttle` | `login:<sha256 do identificador normalizado>` (`login_throttle_key`, a mesma do `/auth/login`) | 5 tentativas / 900 s | o login dá certo |
| `login_ip_throttle` | `admin-login-ip:<ip do cliente>` | 20 falhas / 900 s **com** `trusted_ip_header`; desligado sem ele | nunca — expira com a janela |

Esgotado qualquer um, o formulário volta com `429` e `Retry-After` **antes**
de a senha ser conferida — a senha certa recebe a mesma resposta, então o
`429` não vira oráculo. O identificador desconhecido gasta o orçamento do
mesmo jeito, e o `429` não conta quais endereços existem. A ordem é a do
`make_auth_router`: o orçamento do IP é lido primeiro (um IP bloqueado não
gasta o do identificador), e o do identificador é **reservado** antes da
senha, para uma rajada concorrente não passar inteira por um "ainda não
bloqueado".

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    AttemptThrottle,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin.site import site
from src.api.dependencies import db
from src.core.settings import settings
from src.db.models import UserModel

redis: Redis = Redis.from_url(settings.REDIS_URL)

router = make_admin_router(
    site,
    db=db,
    auth_backend=UserModelAuthBackend(UserModel),
    secret_key=settings.JWT_SECRET,
    login_throttle=AttemptThrottle(redis, max_attempts=5, window_seconds=900),
    login_ip_throttle=AttemptThrottle(redis, max_attempts=20, window_seconds=900),
    trusted_ip_header="x-real-ip",
)
```

!!! warning "Sem `trusted_ip_header`, o orçamento por IP fica desligado"
    Sem o header, o IP é o par da conexão — atrás de nginx, o do próprio
    nginx. Um orçamento por IP ligado ali seria um só para todos os
    operadores, e vinte logins errados de **qualquer um** trancariam todo
    mundo por 15 minutos. Por isso o padrão só o liga com `trusted_ip_header`
    (o header que a sua borda **sobrescreve**: `x-real-ip`,
    `cf-connecting-ip`). Um `login_ip_throttle` passado explicitamente vale
    sempre. O valor do header precisa ser **um** endereço IP
    (`parse_client_ip`); lista, porta, nome ou texto qualquer cai no par da
    conexão, então um cliente não fabrica um orçamento novo por request.

!!! note "O orçamento por identificador também tranca o dono da conta"
    Quem sabe o e-mail de um operador pode gastar as 5 tentativas dele e
    deixá-lo de fora por uma janela, mesmo com a senha certa — a troca que
    todo bloqueio por conta faz, e a mesma do `/auth/login`. O `429` volta
    sozinho quando a janela expira.

!!! info "O padrão conta por processo"
    Os orçamentos padrão vivem num `InMemoryThrottleBackend`: com mais de um
    worker, cada um tem o seu, e o teto vira `5 * workers`. Para um teto de
    verdade, passe um `AttemptThrottle` sobre o Redis, como acima. `False`
    desliga um orçamento (`login_ip_throttle=False`), para quem já limita o
    login em outra camada.

#### 5. Plugue um backend de auth customizado

`AdminAuthBackend` é uma ABC **genérica no principal** — o valor que `authenticate` devolve e que todo o resto dos métodos consome. Troque o default por LDAP / OAuth / IAM externo subclasseando, e diga no parâmetro qual é o seu principal:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError, GoogleOAuthClient

from src.core.settings import settings
from src.db.models import AdminModel

my_oauth_client = GoogleOAuthClient(
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    redirect_uri=settings.GOOGLE_REDIRECT_URI,
)


class OAuthAdminBackend(AdminAuthBackend[AdminModel]):
    """Troca a credencial do formulário do admin por uma identidade OAuth.

    OAuth não tem senha para conferir, então o campo `password` do
    formulário carrega o authorization code que o provedor devolveu no
    redirect. A ABC chama o parâmetro de `password`; o que este backend
    espera ali é o code.
    """

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> AdminModel:
        """Troca o code e aceita só um e-mail de admin conhecido."""
        tokens = await my_oauth_client.exchange_code(password)
        identity = await my_oauth_client.fetch_user(tokens)
        if not identity.email_verified or identity.email != identifier:
            raise AdminAuthError("not an admin")
        result = await session.execute(
            select(AdminModel).where(AdminModel.email == identity.email)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AdminAuthError("not an admin")
        return row

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> AdminModel | None:
        """Recarrega a linha de admin a cada request."""
        return await session.get(AdminModel, UUID(principal_id))

    def principal_id(self, principal: AdminModel) -> str:
        """Devolve o identificador que vai serializado no cookie."""
        return str(principal.id)

    def display_name(self, principal: AdminModel) -> str:
        """Devolve o que o cabeçalho do admin mostra."""
        return principal.email
```

Passe a instância via `auth_backend=` e o resto do pipeline do admin (sessões, dashboard, list, detail) segue funcionando sem mudanças.

!!! tip "O parâmetro `[AdminModel]` é o que torna o contrato conferível"

    Sem ele a classe resolve para `AdminAuthBackend[Any]` — que é como ela se
    comportava antes da v0.263.0, e continua válido. O que o parâmetro compra é
    o erro que aparece quando `authenticate` devolve um tipo e `display_name`
    lê outro. Medido com mypy e com basedpyright: os dois recusam um
    `display_name(self, principal: dict[str, str])` numa classe declarada
    `AdminAuthBackend[LdapEntry]` — o mypy citando Liskov por nome
    (`This violates the Liskov substitution principle`), o basedpyright com
    `reportIncompatibleMethodOverride`.

!!! warning "Subclasse sem parâmetro sob `mypy --strict`"

    `mypy --strict` liga `disallow_any_generics`, então `class MeuBackend(AdminAuthBackend)`
    passa a acusar `Missing type arguments for generic type "AdminAuthBackend"`.
    O runtime não mudou — é só o type-checker. Escreva o seu principal
    (`AdminAuthBackend[MinhaLinha]`) ou, para manter exatamente o comportamento
    anterior, `AdminAuthBackend[Any]`. O `pyproject.toml` que o `tempest new`
    escreve tem `strict = true`.

##### Autorizar por papel e exigir MFA

Quando quem entra no `/admin` é decidido por uma coluna sua (`role ==
"STAFF"`) e não por `is_admin`, não reescreva `authenticate` /
`load_principal` — é ali que mora a checagem de senha resistente a timing.
Sobrescreva só `is_authorized`, que os dois consultam:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseUserModel, MFAMixin, UserModelAuthBackend


class UserModel(MFAMixin, BaseUserModel):
    """Conta do serviço, com papel próprio."""

    __tablename__ = "users"

    role: Mapped[str] = mapped_column(String(16), default="MEMBER")


class StaffAdminBackend(UserModelAuthBackend):
    """Libera o /admin para a equipe, e só com MFA ativa."""

    def is_authorized(self, user: BaseUserModel) -> bool:
        """Entra quem tem papel STAFF, tenha ou não is_admin."""
        return isinstance(user, UserModel) and user.role == "STAFF"


auth_backend: StaffAdminBackend = StaffAdminBackend(UserModel, require_mfa=True)
```

Passe `auth_backend` ao `make_admin_router(...)` como de costume. O que muda:

- **A senha continua primeiro.** Senha errada responde `"Invalid credentials"`
  para qualquer conta; só quem acertou a senha lê `"This account is not
  authorized for /admin"` (papel errado) ou `"MFA enrollment required for
  /admin"` (sem MFA).
- **`require_mfa=True`** recusa no login a conta autorizada e ativa que não
  ativou MFA (`mfa_enabled` falso) — em vez de deixá-la entrar só com a senha.
- **Vale para a sessão também.** `load_principal` consulta os mesmos dois
  critérios a cada request: papel revogado, ou MFA desligada depois do login,
  manda para `/admin/login` no request seguinte.
- `is_authorized` é síncrono de propósito: recebe a linha já carregada e não
  deve ir ao banco.

!!! warning "O admin não tem tela de inscrição em MFA"
    Com `require_mfa=True`, quem ainda não ativou o segundo fator vê, na
    própria página de login, `MFA enrollment required for /admin` (status
    `401`), e não entra. A inscrição acontece **fora** do admin: pelas rotas
    `POST /auth/mfa/enroll` + `POST /auth/mfa/confirm` do
    `make_auth_router` (veja [MFA](mfa.md)), ou na criação da conta pelo
    CLI, com `tempest user create --require-mfa`. Feita a inscrição, o
    próximo login no admin pede o código TOTP em `/admin/mfa`.

#### 6. Painel de tasks — o que está agendado e o que rodou

`TaskPanelService` liga o admin ao que os workers já produzem: a **agenda**
declarada no broker e as **execuções** persistidas por um `JobStore`.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import TaskPanelService
from tempest_fastapi_sdk.tasks import JobStore, TaskQueue

from src.core.settings import settings
from src.db.models import JobModel, UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
tq: TaskQueue = TaskQueue.rabbitmq(settings.RABBITMQ_URL)
jobs: JobStore[JobModel] = JobStore(db, model=JobModel)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        tasks=TaskPanelService(queue=tq, job_store=jobs),
    )
)
```

Isso monta três rotas e um item "Tasks" na barra lateral:

| Rota | O que faz |
| --- | --- |
| `GET /admin/tasks` | execuções recentes (filtráveis por status) + agenda declarada |
| `GET /admin/tasks/{job_id}` | uma execução: progresso, estágio, tentativas, erro |
| `POST /admin/tasks/{job_id}/cancel` | pede parada, quando o status ainda permite |

!!! warning "O painel não mostra a fila do broker"
    Ele mostra o que está **persistido** (linhas de `JobStore`) e o que está
    **declarado** (registro do broker). Profundidade de fila não aparece
    porque a TaskIQ não expõe — e uma tela que insinuasse isso seria pior que
    tela nenhuma.

!!! tip "Qualquer uma das metades serve"
    Só `queue=` mostra a agenda; só `job_store=` mostra as execuções. A seção
    sem fonte não é renderizada, em vez de aparecer vazia e sugerir que não
    há nada para ver. Passar nenhuma das duas levanta `ValueError` na
    construção.

!!! info "A coluna `Schedule` carrega cada gatilho declarado"
    Uma task pode declarar vários gatilhos, e nem todo gatilho é cron: a
    TaskIQ aceita `cron`, `interval` e `time` (disparo único) na mesma lista.
    A coluna mostra uma linha por gatilho, com o fuso ao lado da expressão
    quando a task declarou um:

    | Declarado | Aparece como |
    | --- | --- |
    | `@tq.cron(daily(hour=2), cron_offset=CronOffsetEnum.BRASILIA)` | `0 2 * * *` `-03:00` |
    | `@tq.cron(daily(hour=2))` | `0 2 * * *` |
    | `@tq.interval(timedelta(seconds=30))` | `every 30s` |
    | `schedule=[{"time": quando}]` | `once at 2026-01-01 12:00:00+00:00` |
    | `@tq.task` | `on demand` |

    Mostrar o fuso não é enfeite. Uma expressão cron **parece completa** sem
    ele, então nada convida o leitor a conferir — e `0 2 * * *` com
    `cron_offset=CronOffsetEnum.BRASILIA` dispara às 05:00 UTC. Quem abre o
    painel para saber quando o backup roda erraria por três horas, num lugar
    onde a única outra fonte é o código.

    O painel mostra o que foi **declarado**, não o default da TaskIQ:
    expressão sem fuso aparece sem fuso, não como `(UTC)`.

!!! tip "Renderizando a agenda por conta própria"
    `ScheduledTask.triggers` é a tupla de `TaskTrigger`, na ordem do
    registro, e `TaskTrigger.cron_offset_label` formata o fuso
    (`"-03:00"` para offset fixo, a chave IANA para zona nomeada). Os campos
    planos — `cron`, `interval_seconds`, `cron_offset`, `run_at` — resumem o
    primeiro gatilho de cada tipo, para quem só precisa de um deles. Chave de
    schedule que o SDK ainda não modela fica em `TaskTrigger.extra` em vez de
    ser descartada.

!!! note "Por que não existe coluna \"próxima execução\""
    O `pycron`, que chega junto com a TaskIQ, expõe `is_now` e `has_been` — e
    nenhum `next()`. Calcular a próxima execução exigiria varrer minuto a
    minuto a cada render (até ~44 mil iterações para um cron mensal) ou
    adicionar dependência nova por uma coluna. O painel mostra a expressão —
    com o fuso ao lado, porque sem ele a expressão não é legível.

Para a lista com paginação, filtro e export que o admin já sabe fazer,
registre também o model de jobs — simétrico ao painel de dead-letter:

```python
from tempest_fastapi_sdk import AdminSite
from tempest_fastapi_sdk.tasks import make_job_admin_model

from src.db.models import JobModel

site: AdminSite = AdminSite(title="Ops")
site.register(make_job_admin_model(JobModel))
```

#### 7. Navegador de storage — ver, pré-visualizar e editar arquivos

O admin já grava em object storage (`upload_fields=`), mas o operador não
tinha tela para ver o que o bucket guarda, abrir um arquivo ou apagar um
upload perdido. `StorageBrowserService` é essa tela: lista as pastas,
pré-visualiza o arquivo e faz upload, troca de conteúdo, edição de texto no
lugar, renomeação e exclusão.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import StorageBrowserService
from tempest_fastapi_sdk.storage import LocalObjectStorageClient

from src.core.settings import settings
from src.db.models import UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
store: LocalObjectStorageClient = LocalObjectStorageClient(
    "/var/lib/app/storage",
    public_url="https://api.example.com",
    signing_secret=settings.JWT_SECRET,
)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        storage=StorageBrowserService(store, label="uploads"),
    )
)
```

Com MinIO/S3 é a mesma linha: `StorageBrowserService(AsyncMinIOClient(...))`.
O serviço usa só cinco chamadas — `list_objects`, `stat_object`,
`stream_object`, `put_object` e `remove_object` (o protocolo
`AdminStorageClient`) —, que os dois clientes do SDK implementam.

Isso monta as rotas abaixo e um item "Storage" na barra lateral:

| Rota | O que faz |
| --- | --- |
| `GET /admin/storage?prefix=` | uma pasta: subpastas primeiro, depois arquivos com tamanho, tipo e data; paginada |
| `GET /admin/storage/object?key=` | um arquivo: metadados, pré-visualização, editor de texto e os forms de escrita |
| `GET /admin/storage/raw?key=` | os bytes, para a pré-visualização (`&download=true` força download) |
| `POST /admin/storage/upload` | sobe um arquivo na pasta atual |
| `POST /admin/storage/object/replace` | troca o conteúdo, mantendo a chave e os metadados |
| `POST /admin/storage/object/edit` | salva o editor de texto |
| `POST /admin/storage/object/rename` | move para outra chave, pasta incluída |
| `POST /admin/storage/object/delete` | apaga |

Vamos por partes.

**Pastas não existem.** Em object storage, pasta é só o prefixo da chave até
a `/`. Por isso a pasta aparece assim que um arquivo é gravado debaixo dela e
some quando o último sai. No upload, o campo "Save as" aceita `a/b.txt` e cria
as pastas no caminho.

**A pré-visualização depende do tipo.** Imagem, vídeo, áudio e PDF são
renderizados pelo browser a partir da rota `raw`. Texto (`text/*`, JSON, YAML,
XML, CSV) aparece no próprio HTML, escapado, num `<textarea>` editável até
`max_text_bytes` (256 KiB por padrão). O resto oferece só o download.

!!! danger "SVG e HTML nunca são renderizados"
    A rota `raw` responde na origem do admin. Um HTML renderizado ali roda
    script com a sessão de quem está logado, e SVG carrega `<script>` do mesmo
    jeito. Por isso a rota só concede `inline` aos tipos de
    `INLINE_SAFE_MEDIA_TYPES` — SVG e HTML sempre saem como `attachment` — e
    toda resposta leva `X-Content-Type-Options: nosniff` e uma CSP com
    `sandbox`. Na página, SVG e HTML aparecem como código-fonte escapado.

**A edição de texto recusa escrita concorrente.** O editor envia de volta o
ETag com que abriu; se o arquivo mudou no meio, o save é recusado com
"changed since you opened it" em vez de apagar a escrita do outro. E o
`\r\n` que o browser manda vira `\n` quando o original não tinha `\r\n`,
para salvar um arquivo Unix sem mudança não reescrever toda linha.

**Renomear custa um download e um upload.** Object storage não tem rename:
o serviço copia bytes, tipo e metadados para a chave nova e apaga a antiga.
Chave nova já ocupada é recusada.

!!! tip "Somente leitura"
    `StorageBrowserService(store, read_only=True)` mantém listagem,
    pré-visualização e download, esconde os forms e responde `403` a toda
    escrita. A permissão de abrir a tela é da `surface_policy`, com
    `AdminSurfaceEnum.STORAGE` — veja [Console SQL, tasks e
    logs](#console-sql-tasks-e-logs-surface_policy).

!!! info "Limites"
    `max_upload_bytes` (25 MiB) vale para upload e troca de conteúdo,
    `max_text_bytes` (256 KiB) para pré-visualização e edição de texto, e
    `page_size` (100) para a listagem. Chave com segmento vazio, `.` ou `..`,
    barra invertida, caractere de controle ou mais de 1024 bytes é recusada
    antes de chegar no store. Recusas voltam como mensagem vermelha na
    própria tela.

#### 8. Customizar a aparência — `AdminTheme`

O CSS do admin é todo dirigido por **CSS custom properties** em `:root`. Em vez de forkar a folha de estilo, você passa um `AdminTheme` com **parâmetros tipados e documentados** — cores, logo, favicon, fonte, raio, rodapé, modo escuro — e a SDK injeta um bloco `<style>` no `<head>` (depois do `admin.css`, então ele vence).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # cor primária (links, botões, item ativo)
    accent_hover="#6d28d9",                 # tom de hover do accent
    header_bg="#1e1b4b",                    # fundo do header/sidebar (texto fica claro sozinho)
    radius="10px",                          # raio de botões, inputs, cards, tabelas
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # imagem no header (no lugar do texto)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # superfícies escuras
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
| `header_bg` | `str` | `"#ffffff"` | Fundo do header |
| `sidebar_bg` | ``str | None`` | `None` | Fundo da sidebar (cai pra `header_bg`) |
| `header_fg` | ``str | None`` | `None` | Cor do texto do header e da sidebar (`None` escolhe pelo contraste do fundo) |
| `page_bg` | ``str | None`` | `None` | Fundo do conteúdo (padrão do modo) |
| `radius` | `str` | `"8px"` | Raio de botões, inputs, cards, tabelas |
| `font_family` | ``str | None`` | `None` | `font-family` do painel inteiro |
| `logo_url` | ``str | None`` | `None` | Imagem no header em vez do texto |
| `logo_alt` | `str` | `"Logo"` | `alt` da imagem do logo |
| `favicon_url` | ``str | None`` | `None` | Favicon da aba |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Texto do rodapé |
| `dark_mode` | `bool` | `False` | Superfícies escuras (e o chrome, se `header_bg` for o padrão) |
| `custom_css_url` | ``str | None`` | `None` | Folha de estilo extra, linkada por último |

!!! info "Modo escuro"
    `dark_mode=True` troca as **superfícies de conteúdo** (fundo da página,
    cards, texto, linhas da tabela, inputs, bordas) para uma paleta escura.
    O header e a sidebar escurecem junto enquanto `header_bg` for o padrão
    claro; um `header_bg` seu é mantido. `accent` e as outras cores
    continuam valendo. Um `page_bg` explícito vence o modo escuro do
    conteúdo (as superfícies ficam claras), mas o chrome padrão escurece
    mesmo assim.

!!! info "Texto do header escolhido pelo fundo"
    O header e a sidebar são claros por padrão. Com um `header_bg` (ou
    `sidebar_bg`) em hex escuro, o texto deles passa para claro sozinho —
    a SDK calcula a luminância do fundo. Cor que não é hex
    (`"rebeccapurple"`, `rgb(...)`) é tratada como escura; se o palpite
    errar, fixe a cor com `header_fg`.

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

## Recap

- `AdminSite` + `AdminModel` transformam os seus models numa interface de CRUD
  sem você escrever template: a declaração é a tela.
- `@admin_action` põe operação de domínio na lista, e o retorno
  `AdminActionResult` é o que o operador lê de volta. O painel commita o que
  a ação deixou na sessão; `schema=` pede dados antes de rodar, e
  `action_policy=` libera ação por ação, sem `EDIT` no model.
- `audit_model=` renderiza a linha do tempo de quem mudou o quê no próprio
  detail, e `inlines=` traz os filhos 1-N para a mesma página.
- `autocomplete_fields=` troca o `<select>` que não escala por busca HTMX —
  necessário no instante em que a FK tem milhares de linhas.
- `dashboard_cards=` são métricas de negócio, não de sistema; `lenses=` são as
  visões salvas que o operador usaria de novo amanhã.
- `location_maps=[BRLocationMap(...)]` desenha onde estão as linhas de um model com `BRLocationMixin`: mapa do Brasil por estado e cidade, offline.
- `access_policy=` é RBAC por (principal, ação, model): sem ele, quem entra no
  admin pode tudo.
- `storage=StorageBrowserService(...)` abre o bucket na tela: lista,
  pré-visualiza (SVG e HTML só como código-fonte), sobe, edita texto,
  renomeia e apaga — `read_only=True` deixa só a leitura.
- `AdminTheme` cobre a aparência por campo tipado; `can_import=True` abre o
  import CSV em dois passos: a prévia valida e ensaia sem gravar, e o
  confirmar grava o arquivo assinado, tudo ou nada.
- `row_scope=` filtra **quais linhas** o principal alcança em toda rota do
  model; fora do escopo é `404`, e escrita que tira a linha do escopo
  volta.
- O form de edição traz a trava otimista de graça: save sobre uma versão
  velha volta com `409` e os valores do operador, sem gravar.
- `AdminSite(locale="pt-BR")` põe o painel em português; os atalhos de
  data do filtro saem traduzidos e no fuso do model.
- `exclude_fields=` tira a credencial que não é senha de toda superfície
  do painel; `readonly_fields` só tranca a escrita.
- `display_timezone=` é o que transforma o campo de datetime em widget:
  a tela lê e escreve no fuso do operador, a coluna continua em UTC.
- `m2m_fields=` edita um `secondary` como multi-select, e o vínculo que o
  operador não alcança fica; `related_counts=` conta os filhos por linha
  com um `GROUP BY` por página.
- `can_duplicate=True` abre o create preenchido com a cópia, e
  `bulk_edit_fields=` aplica um valor a toda a seleção num `UPDATE` só,
  com rollback se ele tirar linha do escopo.
- `AdminSite(recent_activity=N)` liga o feed das últimas mudanças
  auditadas no dashboard; o extra `[spreadsheet]` acrescenta o `.xlsx` ao
  export e ao import.
