# Busca global

Use para buscar um termo em todo model com `search_fields` pela caixa de busca no topo do painel.

Parte da receita [Painel admin](../admin.md).

Quando algum model que o operador vê tem `search_fields`, o topo do painel
ganha uma caixa de busca. Ela vai para `GET /admin/search?q=`, que procura
o termo em **todo** model com `search_fields`, com a mesma condição da
busca da listagem (cada palavra num `ILIKE`, em qualquer um dos campos), e
mostra até `ADMIN_SEARCH_RESULTS_PER_MODEL` (5) linhas por model, cada uma
com link para o detail. Quando há mais, o grupo leva **Ver todos** para a
listagem do model já filtrada com `?q=`. O limite é ajustável:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        search_results_per_model=10,
    ),
)
```

A busca respeita o que o resto do painel respeita: model sem `VIEW` na
`access_policy` não entra, e cada model é consultado com o próprio
`row_scope` — uma linha fora do escopo não aparece. Model com
`repository_class` é consultado pelo `paginate` do repositório (que roda um
`COUNT` por model), com a página limitada ao `max_page_size` dele. Termo vazio
(ou só espaços) mostra a página sem rodar consulta, e termo com mais de
`ADMIN_SEARCH_MAX_QUERY_LENGTH` (200) caracteres ou mais de
`ADMIN_SEARCH_MAX_TERMS` (8) palavras responde `400` com o aviso, também
sem consulta — cada palavra vira um `ILIKE` por campo de cada model.

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
    [Segurança](../security.md#scheme-publico-atras-de-proxy-server_public_scheme).
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
    `make_auth_router` (veja [MFA](../mfa.md)), ou na criação da conta pelo
    CLI, com `tempest user create --require-mfa`. Feita a inscrição, o
    próximo login no admin pede o código TOTP em `/admin/mfa`.

## 6. Painel de tasks — o que está agendado e o que rodou

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

## 7. Navegador de storage — ver, pré-visualizar e editar arquivos

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
    logs](permissions.md#console-sql-tasks-e-logs-surface_policy).

!!! info "Limites"
    `max_upload_bytes` (25 MiB) vale para upload e troca de conteúdo,
    `max_text_bytes` (256 KiB) para pré-visualização e edição de texto, e
    `page_size` (100) para a listagem. Chave com segmento vazio, `.` ou `..`,
    barra invertida, caractere de controle ou mais de 1024 bytes é recusada
    antes de chegar no store. Recusas voltam como mensagem vermelha na
    própria tela.

## 8. Customizar a aparência — `AdminTheme`

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
