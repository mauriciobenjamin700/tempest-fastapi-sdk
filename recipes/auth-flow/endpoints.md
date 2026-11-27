# Endpoints

Consulte a tabela de todos os endpoints com payload e comportamento: sistema fechado, conta criada pelo admin, campos extras no cadastro e limite de tentativas.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

| Método | Path | Body / Output | Comportamento |
|--------|------|---------------|---------------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Cria user. Não montado quando `AUTH_SIGNUP_ENABLED=false` (v0.272.0+). Emite e-mail (modos A/B) **ou** devolve link no body (modo C). Se `AUTH_AUTO_ACTIVATE=True`, user nasce ativo e JWT pair volta direto (modo D). |
| POST | `/auth/activate/{token}` | — → `ActivationResponseSchema` | Consome token + `is_active=True` + emite JWT pair. Conta **recém-cadastrada** não tem fator, então nada muda; mas se o token veio de `/auth/activation/request` para uma conta desativada que tinha MFA inscrito, responde o desafio (`mfa_required=true` + `mfa_token`) em vez do par. |
| POST | `/auth/activation/request` *(v0.297.0+)* | `ActivationRequestSchema` → `ActivationResendResponseSchema` | **Não autenticado.** Reemite o link de ativação de uma conta que nunca ativou. Sempre 202 + corpo genérico. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + senha → JWT pair. Erros genéricos (não enumera contas). |
| GET | `/auth/me` *(v0.198.0+)* | — → `AuthUserSchema` | **Autenticado.** Devolve a conta dona do bearer token. Nunca serializa o hash da senha: o handler entrega o modelo inteiro e o `response_model` filtra. Troque o schema por um seu com `me_response_model=` pra expor colunas próprias. |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Sempre HTTP 202 + corpo genérico. Link via e-mail (A/B) ou no body (C). |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consome token + grava nova senha + revoga as sessões + emite JWT pair. Com MFA inscrito na conta, responde o desafio (`mfa_required=true` + `mfa_token`) em vez do par — conclua em `/auth/mfa/verify`. |
| POST | `/auth/password-change` | `PasswordChangeSchema` → `LoginResponseSchema` | **Autenticado** (bearer token). Troca a própria senha: confirma a senha atual (e o `code` numa conta com MFA) + grava a nova. Sem token de e-mail. |
| POST | `/auth/email-change/request` *(v0.92.0+)* | `EmailChangeRequestSchema` → `EmailChangeResponseSchema` | **Autenticado.** Confirma a senha atual + envia link de confirmação pro **novo** e-mail. Sempre 202. |
| POST | `/auth/email-change/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consome o token + aplica o novo e-mail + (opcional) avisa o e-mail antigo. |
| POST | `/auth/email-verify/request` *(v0.92.0+)* | — → `EmailChangeResponseSchema` | **Autenticado.** Reenvia link de verificação pro e-mail **atual**. |
| POST | `/auth/email-verify/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consome o token + marca a conta ativa. |
| POST | `/auth/email-recovery/request` *(v0.92.0+, opt-in)* | `EmailRecoveryRequestSchema` → `EmailChangeResponseSchema` | **Não autenticado.** Recupera conta cujo e-mail se perdeu: senha (+ MFA se inscrito). Só montado com `AUTH_EMAIL_RECOVERY_ENABLED=True`. Sempre 202. |
| GET | `/auth/oauth/{provider}/login` *(v0.273.0+)* | — → `302` | Sorteia o `state`, grava no cookie `HttpOnly` e redireciona ao provedor. Só montado com `AUTH_OAUTH_ENABLED=true`. Veja a [receita de login social](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/oauth.md). |
| GET | `/auth/oauth/{provider}/callback` *(v0.273.0+)* | — → `LoginResponseSchema` | Confere o `state`, troca o `code`, resolve `(provider, subject)` no banco e emite **o mesmo par JWT** do `/auth/login` — `typ`, refresh opaco, rotação e `/auth/logout` incluídos. Conta com TOTP recebe o desafio (`mfa_required=true` + `mfa_token`), como no `/auth/login` *(v0.314.0+)*. |
| GET | `/auth/oauth/accounts` *(v0.273.0+)* | — → `list[OAuthAccountSchema]` | **Autenticado.** Provedores ligados à conta. Lista vazia é 200, não 404. |
| POST | `/auth/oauth/accounts/unlink` *(v0.273.0+)* | `OAuthUnlinkSchema` → `204` | **Autenticado.** Desliga um provedor. 404 quando não está ligado nesta conta. |
| POST | `/auth/refresh` *(v0.65.0+)* | `RefreshSchema` → `LoginResponseSchema` | Troca um **refresh token** válido por um JWT pair novo. **Sem email/senha.** Rejeita access token replayado (401) e conta inativa (403). |

!!! tip "`password-reset/confirm` vs `password-change` — qual é qual?"
    São fluxos **diferentes**, não confunda:

    - **`/auth/password-reset/confirm`** — o usuário **esqueceu** a senha.
      Ele não está logado; prova identidade com o **token** que recebeu
      por e-mail. (Veja `/auth/password-reset/request` antes.)
    - **`/auth/password-change`** — o usuário **lembra** a senha e está
      **logado**. Manda o `access_token` no header `Authorization:
      Bearer …` e reconfirma a `current_password`. Não envolve e-mail
      nem token de reset. Responde **200**: todas as sessões são
      revogadas e quem chamou recebe um par novo (ou, numa conta com MFA
      sem `code`, o desafio do login).

## Sistema fechado — sem porta de cadastro *(v0.272.0+)*

Nem todo serviço quer `POST /auth/signup` no ar. Num sistema **fechado** —
conta criada só por administrador, nunca por quem chega na porta — a rota de
cadastro público é exatamente o buraco que não se quer aberto.

Desligue por variável de ambiente:

```dotenv
AUTH_SIGNUP_ENABLED=false
```

Ou por router, quando o mesmo processo monta mais de um e só um deles é
fechado:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserAuthService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

app = FastAPI()

db = AsyncDatabaseManager(settings.DATABASE_URL)

auth_service = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        allow_signup=False,
    ),
)
```

O argumento vence a setting nos dois sentidos; `allow_signup=None` (o
default) delega para `AUTH_SIGNUP_ENABLED`. É o mesmo par que
`token_delivery` já formava com `AUTH_TOKEN_DELIVERY`.

O que o serviço passa a responder:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"quem-chegou@example.com","password":"strong-pass-12"}'
404
```

!!! warning "Filtrar `router.routes` depois de montar não é equivalente"
    O workaround que sobrava antes era remover a rota da lista já
    construída:

    ```python
    router.routes = [
        route for route in router.routes
        if getattr(route, "path", "") != "/auth/signup"
    ]
    ```

    Ele casa uma **string de path** — quebra em silêncio no dia em que o
    caminho mudar — e não diz nada ao schema: o `/openapi.json` continua
    anunciando uma porta que não existe mais. Com `AUTH_SIGNUP_ENABLED`
    a rota nunca é registrada, então some da aplicação **e** do schema.

!!! note "A ativação continua montada — de propósito"
    Desligar o signup **não** desmonta `/auth/activate/{token}`. Conta
    aberta por um fluxo com link de ativação ainda precisa dele, e é esse
    endpoint que consome o token do e-mail. Nada além de
    `POST /auth/signup` muda de lugar. Conta aberta pelo painel com
    `admin_create_user` já nasce ativa — veja a próxima seção.

**Recap.** `AUTH_SIGNUP_ENABLED=false` fecha o cadastro público sem tocar em
nenhum outro endpoint; `allow_signup=` sobrescreve por router; a rota some do
OpenAPI junto, que é o que o filtro manual nunca entregou.

## Conta criada pelo administrador, sem e-mail *(v0.364.0+)*

Fechar o cadastro resolve metade do sistema fechado. A outra metade é o
painel: o administrador abre a conta com e-mail e senha, e às vezes troca a
senha de alguém. Ninguém recebe e-mail nesse caminho — e por isso ele não
pode passar pelos fluxos de link.

Sem mailer em produção (`ENV` ausente conta como produção), `signup` e
`request_password_reset` respondem `503 INTEGRATION_DISABLED`: a conta cujo
link de ativação nunca chega não é criada, e o reset que ninguém recebe não
é emitido. É a regra certa para o usuário, e é por isso que compor
`signup` + `activate` ou `request_password_reset` + `confirm_password_reset`
para o painel quebra.

O `UserAuthService` tem os dois métodos do painel, sem token e sem e-mail:

```python
# main.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    AuthUserSchema,
    BaseUserModel,
    DatabaseSettings,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)


class Settings(DatabaseSettings, JWTSettings, AuthSettings):
    """Settings lidas do ambiente (e do `.env`)."""


class UserModel(BaseUserModel):
    """A tabela de contas."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
get_current_user = auth_service.current_user_dependency()


class AccountCreateSchema(BaseModel):
    """O que o administrador preenche para abrir uma conta."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)
    name: str | None = None


class PasswordSetSchema(BaseModel):
    """A senha nova que o administrador define."""

    new_password: str = Field(min_length=1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Abre o banco e cria as tabelas no startup.

    Args:
        app (FastAPI): A aplicação que está subindo.

    Yields:
        None: Devolve o controle ao FastAPI enquanto o app atende.
    """
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        allow_signup=False,
    ),
)


@app.post("/api/admin/accounts", status_code=201)
async def create_account(
    payload: AccountCreateSchema,
    current: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthUserSchema:
    """Abre uma conta já ativa, com a senha escolhida pelo administrador.

    Args:
        payload (AccountCreateSchema): E-mail, senha e nome da conta nova.
        current (UserModel): Quem chama; precisa ser administrador.
        session (AsyncSession): A sessão da requisição.

    Returns:
        AuthUserSchema: A visão pública da conta criada.
    """
    auth_service.require_admin(current)
    user = await auth_service.admin_create_user(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    await session.commit()
    return AuthUserSchema.model_validate(user)


@app.put("/api/admin/accounts/{user_id}/password", status_code=204)
async def set_account_password(
    user_id: UUID,
    payload: PasswordSetSchema,
    current: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(db.session_dependency),
) -> None:
    """Troca a senha de uma conta e encerra as sessões dela.

    Args:
        user_id (UUID): A conta cuja senha muda.
        payload (PasswordSetSchema): A senha nova.
        current (UserModel): Quem chama; precisa ser administrador.
        session (AsyncSession): A sessão da requisição.
    """
    auth_service.require_admin(current)
    await auth_service.admin_set_password(
        session,
        user_id=user_id,
        new_password=payload.new_password,
    )
    await session.commit()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` aqui é atalho de demonstração"
    O exemplo cabe num arquivo só, então cria as tabelas direto no
    lifespan. Num serviço, o lifespan chama `sync_schema_async()` sobre as
    migrations do Alembic: `create_tables()` não acrescenta coluna em
    tabela que já existe. Veja
    [Migrations »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/migrations.md#3-sincronize-o-schema-no-lifespan).

O que cada rota faz:

- **`admin_create_user`** cria a conta **já ativa**. O caminho é o do
  `signup`: e-mail normalizado, a mesma política de senha, os mesmos campos
  protegidos em `fields=`, e o mesmo `ConflictException` (`409`) para e-mail
  repetido. `is_admin=True` é parâmetro próprio, porque `fields=` recusa a
  coluna. `email_verified_at` fica vazio: quem digitou o endereço foi o
  administrador, e ninguém provou a caixa.
- **`admin_set_password`** troca a senha com a mesma política do reset e,
  por default, **revoga as sessões e as passkeys** da conta, como o
  `confirm_password_reset`: outra pessoa escolheu a senha, então o acesso
  antigo acaba. `revoke_sessions=False` e `revoke_passkeys=False` são o
  opt-out explícito. Id inexistente é `404`.
- Os dois só fazem `flush`; o `commit` é da rota.

Rodando o app acima sem `ENV` e com `email=None`, com uma conta
administradora já logada:

```console
POST /api/admin/accounts {"email": "Maria@Example.com", ...}  -> 201, "email": "maria@example.com", "is_active": true
POST /api/admin/accounts {"email": "maria@example.com", ...}  -> 409
PUT  /api/admin/accounts/<id>/password                       -> 204
POST /auth/login com a senha nova                             -> 200
POST /auth/signup                                             -> 404
```

!!! warning "A autorização é da rota"
    Os métodos confiam em quem chama. Proteja a rota com
    `auth_service.require_admin(current)` (ou a sessão do painel), como no
    exemplo — sem isso, qualquer conta logada abre conta e troca senha.

**Recap.** Conta nascida no painel usa `admin_create_user`; senha trocada
pelo administrador usa `admin_set_password`. Nenhum dos dois depende de
mailer, e os dois mantêm a política de senha, o conflito de e-mail e a
revogação de sessão dos fluxos de link.

## Conta que nasce com mais que e-mail e senha *(v0.278.0+)*

Quase nenhum produto tem uma conta que é só `email`, `password` e `name`. Tem o
telefone, tem o CPF, tem a flag que separa cliente de produtor. Até aqui a saída
era desligar o `POST /auth/signup` e escrever a rota na mão — e a rota na mão
tinha que reconstruir a política de senha, o 409 de e-mail duplicado, o ramo de
ativação e a emissão do par JWT. Quatro coisas que a rota do SDK já acerta, e
que passam a driftar uma release por vez.

Um argumento resolve o caso comum: **`signup_schema`** troca o corpo da
requisição, e todo campo novo dele que também é coluna do seu `UserModel` é
gravado na linha **antes** do insert *(v0.305.0+)*. Para o que não é coluna —
um aceite de termos, um campo derivado — existe **`on_signup`**.

```python
# src/api/app.py

from fastapi import FastAPI
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthUserSchema,
    SignupSchema,
    UserAuthService,
    ValidationException,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel


class ProducerSignupSchema(SignupSchema):
    """O cadastro deste produto: e-mail, senha, nome — e o resto."""

    phone: str = Field(max_length=20)
    is_producer: bool = Field(default=False)
    accept_terms: bool = Field(default=False)


class ProducerMeSchema(AuthUserSchema):
    """O que o ``GET /auth/me`` devolve: a conta e as colunas deste produto."""

    phone: str
    is_producer: bool


async def require_terms(
    session: AsyncSession,
    user: UserModel,
    payload: ProducerSignupSchema,
) -> None:
    """Recusa o cadastro sem aceite dos termos.

    Args:
        session (AsyncSession): A transação em que a linha foi inserida.
        user (UserModel): A instância recém-criada.
        payload (ProducerSignupSchema): O corpo validado.

    Raises:
        ValidationException: Quando ``accept_terms`` é falso.
    """
    if not payload.accept_terms:
        raise ValidationException(message="accept the terms to sign up")


app = FastAPI()

db = AsyncDatabaseManager(settings.DATABASE_URL)

auth_service = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        signup_schema=ProducerSignupSchema,
        on_signup=require_terms,
        me_response_model=ProducerMeSchema,
    ),
)
```

Aqui `phone` e `is_producer` são colunas do `UserModel` (`phone` é
`NOT NULL` e `UNIQUE`), então chegam à linha sem hook nenhum. `accept_terms`
não é coluna: fica só no `payload` que o `on_signup` recebe. E o
`me_response_model=ProducerMeSchema` faz o `GET /auth/me` devolver as duas
colunas novas junto das que o `AuthUserSchema` já traz — e continua sem o
`hashed_password`, que nenhum dos dois schemas declara.

Com `AUTH_AUTO_ACTIVATE=true` no `.env` a conta nasce ativa e o signup já devolve o par de JWT. No default (`false`) a mesma chamada responde `"activation_required":true` com `access_token` e `refresh_token` em `null`, e o par só sai no `POST /auth/activate/{token}`:

```console
$ curl -s -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"ana@example.com","password":"strong-pass-12","phone":"5511999999999","is_producer":true,"accept_terms":true}'
{"user_id":"0e5cf2fc-…","activation_required":false,"activation_url":null,"access_token":"eyJ…","refresh_token":"eyJ…"}
```

Um segundo cadastro com o mesmo telefone responde `409`, com a coluna em
`details` e em `field` — não um `500` de `IntegrityError`:

```console
$ curl -s -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"bia@example.com","password":"strong-pass-12","phone":"5511999999999","accept_terms":true}'
{"detail":"phone already in use","code":"CONFLICT","details":{"columns":["phone"],"constraint":null},"field":"phone"}
```

O `constraint` sai `null` no SQLite, que não nomeia a constraint de unicidade;
no PostgreSQL vem o nome (`users_phone_key`, por exemplo). O `detail` acima é o
de um app sem catálogo; com `register_exception_handlers(app,
catalog=default_message_catalog())` ele sai traduzido (`Conflito de recurso`),
e `code`, `details` e `field` não mudam.

!!! warning "Coluna protegida nunca vem do corpo"
    `id`, `email`, `hashed_password`, `is_active`, `is_admin`,
    `email_verified_at` e as colunas do `MFAMixin` (`totp_secret`,
    `totp_enabled_at`, `totp_last_step`), e as colunas de controle que o SDK
    mantém — `last_login_at` (gravada pelo login), `created_at` /
    `updated_at` (relógio do banco), `deleted_at` (`SoftDeleteMixin`: uma
    linha nascida apagada some de toda consulta) e `created_by` /
    `updated_by` (`AuditMixin`: o cadastro alegaria outro autor) — o
    `SIGNUP_PROTECTED_FIELDS` — são do SDK, e toda coluna que o seu model declara em `__secret_columns__`
    entra junto (`service.signup_protected_fields()` devolve o conjunto do
    seu model). Um `signup_schema` com um campo `is_admin` não promove
    ninguém, e um com `totp_enabled_at` não liga fator nenhum: a rota
    descarta esses nomes antes de gravar. Chamando o service direto,
    `signup(fields={"is_admin": True})` levanta `ValueError`.

Anote o hook com as **suas** classes — o model concreto e o schema concreto —
como acima: é o que faz `payload.accept_terms` passar no type-checker do seu
lado.

O schema **precisa** herdar de `SignupSchema` — a rota lê `email`, `password` e
`name` pelo nome, então um corpo que não os traz derruba o `make_auth_router`
na hora da montagem, não no primeiro request. Os campos novos entram no
`/openapi.json` junto: o cliente gerado enxerga `phone` e `is_producer` sem
ninguém escrever schema à mão.

!!! tip "Se o seu service sobrescreve `signup`"
    Declare `fields: Mapping[str, Any] | None = None` no override e repasse para
    `super().signup(...)`: é por ele que as colunas do `signup_schema` chegam
    antes do insert. Um override sem `fields` continua funcionando — a rota o
    chama sem esse argumento, como antes da 0.305.0, e avisa no log, na
    montagem do router, quais colunas ficaram para o `on_signup` preencher.

??? info "Sem router: `signup(fields=...)`"
    O mesmo caminho está no service, para quem monta a própria rota:

    ```python
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def create_driver(
        service: UserAuthService,
        session: AsyncSession,
        email: str,
        password: str,
        phone: str,
    ) -> None:
        """Cria o motorista com o telefone obrigatório já no insert.

        Args:
            service (UserAuthService): O service de auth.
            session (AsyncSession): A sessão da requisição.
            email (str): E-mail da conta.
            password (str): Senha em texto puro.
            phone (str): Telefone, coluna ``NOT NULL`` e única.
        """
        await service.signup(
            session,
            email=email,
            password=password,
            fields={"phone": phone},
        )
        await session.commit()
    ```

    Chave que não é coluna do model vira `ValueError` antes de qualquer
    escrita. Coluna `NOT NULL` esquecida também vira `ValueError`, com o nome
    dela na mensagem — é defeito de montagem, não erro do cliente.

!!! danger "O hook roda **antes** do commit, e isso é o ponto"
    `on_signup` é aguardado logo depois do insert e antes do
    `session.commit()`, na **mesma** transação. Um hook que levanta desfaz a
    conta junto.

    A alternativa — escrever os campos depois, num segundo commit — é o defeito
    clássico: um CPF recusado deixa para trás uma conta com e-mail, senha e
    nenhum dos campos que a tornam usável. Ela não aparece em lugar nenhum do
    produto, mas ocupa o e-mail (que é `UNIQUE`), responde ao
    `POST /auth/password-reset/request` e bloqueia o cadastro correto com um
    409 que ninguém consegue explicar.

!!! tip "O que ainda não muda"
    A resposta continua sendo `SignupResponseSchema`. Se o cliente precisa do
    perfil completo logo depois do cadastro, o `GET /auth/me` já responde com
    ele — e aceita o seu próprio schema por `me_response_model`.

**Recap.** `signup_schema` é o contrato de entrada — subclasse de
`SignupSchema`, visível no OpenAPI —, e os campos dele que são coluna entram na
linha antes do insert. `on_signup` é para o resto, dentro da transação do
insert. Nada mais precisa sair do SDK para o seu serviço.


## Limite de tentativas no login e no cadastro

Sem limite, `POST /auth/login` responde `401` a cada senha errada para sempre —
força bruta é só questão de paciência — e `POST /auth/signup` cria conta sem
teto. O router já traz os dois freios, no molde do `mfa_throttle`:

| Parâmetro | Chave | Conta | Default |
| --- | --- | --- | --- |
| `login_throttle` | e-mail normalizado (SHA-256) | toda tentativa, **zerada no login certo** | ligado: 5 por 900 s, em memória |
| `login_ip_throttle` | IP do cliente | só credencial errada; login certo **não** zera | desligado |
| `signup_throttle` | IP do cliente | toda tentativa, certa ou não | desligado |

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AttemptThrottle,
    AuthSettings,
    BaseUserModel,
    InMemoryThrottleBackend,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class UserModel(BaseUserModel):
    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")


async def main() -> None:
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    service: UserAuthService = UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(),
        email=None,
    )
    app: FastAPI = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=db.session_dependency,
            login_ip_throttle=AttemptThrottle(
                InMemoryThrottleBackend(), max_attempts=20, window_seconds=900
            ),
            signup_throttle=AttemptThrottle(
                InMemoryThrottleBackend(), max_attempts=10, window_seconds=3600
            ),
            trusted_ip_header="x-real-ip",
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/signup",
            json={"email": "ana@example.com", "password": "senha-forte-123"},
        )
        for password in ["senha-errada-123"] * 5 + ["senha-forte-123"]:
            response: Response = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": password},
            )
            print(response.status_code, response.headers.get("retry-after"))
        print(response.json())
    await db.disconnect()


asyncio.run(main())
```

Saída:

```text
401 None
401 None
401 None
401 None
401 None
429 900
{'detail': 'too many login attempts, try again later', 'code': 'LOGIN_THROTTLED', 'details': {'retry_after_seconds': 900}}
```

A sexta tentativa leva `429` **com a senha certa**: o orçamento é reservado
antes de a senha ser conferida, então uma rajada de chutes em paralelo não
passa toda junta por um "ainda não bloqueado". O login certo antes do limite
zera a contagem do e-mail.

!!! check "O 429 não revela se o e-mail existe"
    O orçamento do e-mail é gasto do mesmo jeito para um endereço sem conta, e o
    `detail` é o mesmo texto nos dois casos. Quem sonda o login aprende que
    errou demais, não quais e-mails estão cadastrados.

!!! warning "Por que os limites por IP vêm desligados"
    Atrás de um proxy, o IP que o app vê é o do **proxy** — sem
    `trusted_ip_header`, todo cliente cai no mesmo balde, e um orçamento por IP
    trava o login (ou o cadastro) de todo mundo de uma vez. Ligue
    `login_ip_throttle` e `signup_throttle` junto com o `trusted_ip_header` que
    a sua borda sobrescreve (`"x-real-ip"`), nunca com `X-Forwarded-For`.

Mais quatro coisas que valem saber:

- **Com mais de um worker, passe um backend compartilhado.** O default conta
  em memória, por processo; `AttemptThrottle(redis, max_attempts=5,
  window_seconds=900)` divide o contador entre réplicas.
- **`login_throttle=False` desliga** o limite por e-mail, para quem já limita
  o login em outra camada.
- **Bloquear por e-mail tem custo**: quem conhece o e-mail de alguém consegue
  gastar o orçamento e deixar o dono de fora por uma janela. É a troca de todo
  bloqueio por conta; a janela curta é o que a deixa aceitável.
- **O resto do router não muda.** `/auth/me`, `/auth/refresh` e as demais rotas
  não passam por nenhum desses limites, e o OpenAPI só declara o `429` nas
  rotas de login e em `/auth/signup` quando o limite correspondente está ligado.

**Recap.** O login vem protegido por e-mail sem você fazer nada; os limites por
IP você liga quando sabe de onde vem o IP real.
