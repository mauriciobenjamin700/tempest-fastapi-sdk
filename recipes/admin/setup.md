# Montar o painel

Use para subir o painel: modelo de usuário, registro das classes de admin (ou `automap`), o router com tudo o que ele monta, os defaults de segurança da sessão e um backend de auth customizado.

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

    Edição inline in-place dos filhos: veja [`Inline(editable=True)`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/forms.md#edicao-in-place-editabletrue).

## 4. Defaults de segurança de sessão

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
    [Segurança](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/security.md#scheme-publico-atras-de-proxy-server_public_scheme).
    Enquanto falta, o `GET /admin/login` loga um `WARNING` dizendo isso.

### Logar em teste

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

### Orçamento de falhas no login

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

## 5. Plugue um backend de auth customizado

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

### Autorizar por papel e exigir MFA

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
    `make_auth_router` (veja [MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md)), ou na criação da conta pelo
    CLI, com `tempest user create --require-mfa`. Feita a inscrição, o
    próximo login no admin pede o código TOTP em `/admin/mfa`.
