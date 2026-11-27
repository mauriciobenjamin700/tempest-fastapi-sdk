# E-mail desligado e SMTP fora do ar

Use para saber o que cada rota responde com `EMAIL_ENABLED=false` ou com o SMTP falhando, e quando mandar e-mail em background.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

## E-mail desligado

Num deploy por partes o SMTP pode chegar depois do resto
(`EMAIL_ENABLED=false`, veja
[Ligar e desligar integrações](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/system-checks.md#ligar-e-desligar-integracoes-deploy-por-partes)).
O risco aqui é concreto: com `AUTH_AUTO_ACTIVATE=false`, um cadastro cria a
conta inativa e manda o link de ativação — que nunca chega. A conta fica
presa, e o e-mail dela fica "em uso" para sempre.

Por isso o `UserAuthService` confere o mailer **antes** de gravar qualquer
coisa. Com `EmailUtils(**settings.email_kwargs())` construído sob
`EMAIL_ENABLED=false`, toda rota que mandaria um link responde `503
INTEGRATION_DISABLED`:

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    BaseUserModel,
    EmailSettings,
    EmailUtils,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class Settings(EmailSettings, JWTSettings, AuthSettings):
    """Settings com o e-mail ainda não configurado."""


class UserModel(BaseUserModel):
    """A tabela de contas."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings(
    EMAIL_ENABLED=False,
    AUTH_AUTO_ACTIVATE=False,
    JWT_SECRET="troque-por-um-segredo-longo-de-verdade-0123456789",
)
db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=EmailUtils(**settings.email_kwargs()),
)

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))


async def main() -> None:
    """Tenta um cadastro com o e-mail desligado."""
    await db.connect()
    await db.create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={"email": "ana@acme.com.br", "password": "senha-forte-123"},
        )
    print(response.status_code, response.json())
    await db.disconnect()


asyncio.run(main())
```

```text
503 {'detail': 'The email integration is disabled in this environment', 'code': 'INTEGRATION_DISABLED', 'details': {'integration': 'email'}}
```

Nenhuma linha foi gravada: nem a conta, nem o token. As rotas recusadas
são as que mandam e-mail — `/signup` (só com `AUTH_AUTO_ACTIVATE=false`),
`/activation/request`, `/password-reset/request`, `/email-change/request`,
`/email-verify/request`, `/email-recovery/request` e, com
`AUTH_EMAIL_CHANGE_NOTIFY_OLD=true`, `/email-change/confirm` (antes de
consumir o token, que segue valendo quando o e-mail voltar). Login,
refresh, logout e MFA seguem normais.

!!! info "Quando o e-mail desligado não recusa nada"
    Sem mailer (`email=None`) ou com `AUTH_RETURN_TOKEN_IN_RESPONSE=true`
    o link viaja no corpo da resposta, então não há e-mail a mandar e o
    fluxo segue como sempre. O mesmo vale para o cadastro com
    `AUTH_AUTO_ACTIVATE=true`: a conta nasce ativa e não precisa de link.

!!! tip "A recusa vem antes da busca"
    Em `/password-reset/request` e `/email-recovery/request` o `503` sai
    antes de procurar a conta, então endereço conhecido e desconhecido
    recebem a mesma resposta — o interruptor não vira oráculo de
    enumeração.

## SMTP fora do ar

`EMAIL_ENABLED=false` é a escolha de rodar sem e-mail. O caso oposto é o
e-mail **configurado e falhando**: o host não resolve, a conexão é
recusada, o handshake TLS quebra (`SMTP_USE_SSL=true` na porta `587`
responde `[SSL: WRONG_VERSION_NUMBER]` em todo envio), o login é negado.
Antes, isso subia como erro do `aiosmtplib` e a rota respondia um `500`
pelado, com o traceback logado como exceção não tratada.

Agora o `EmailUtils.send` traduz toda falha de transporte em
`EmailDeliveryException` — `503`, `code="EMAIL_UNAVAILABLE"`, sem
`details` — e cada fluxo de auth decide o que fazer com ela:

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    BaseUserModel,
    EmailSettings,
    EmailUtils,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class Settings(EmailSettings, JWTSettings, AuthSettings):
    """Settings com um SMTP que não atende."""


class UserModel(BaseUserModel):
    """A tabela de contas."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings(
    SMTP_HOST="127.0.0.1",
    SMTP_PORT=2525,
    SMTP_TIMEOUT_SECONDS=5.0,
    AUTH_AUTO_ACTIVATE=False,
    JWT_SECRET="troque-por-um-segredo-longo-de-verdade-0123456789",
)
db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=EmailUtils(**settings.email_kwargs()),
)

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))


async def main() -> None:
    """Tenta um cadastro com o SMTP fora do ar."""
    await db.connect()
    await db.create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/signup",
            json={"email": "ana@acme.com.br", "password": "senha-forte-123"},
        )
    print(response.status_code, response.json())
    await db.disconnect()


asyncio.run(main())
```

```text
503 {'detail': 'The e-mail server could not be reached; try again later', 'code': 'EMAIL_UNAVAILABLE', 'details': {}}
```

Nenhuma linha foi gravada: o `flush` da conta e do token não chegou ao
`commit`, e a sessão da requisição desfez os dois. O mesmo endereço pode
se cadastrar de novo quando o servidor voltar.

### O que cada rota responde

| Rota | Com o SMTP falhando | O que fica gravado |
| --- | --- | --- |
| `/signup` | `503 EMAIL_UNAVAILABLE` | nada: nem conta, nem token |
| `/email-change/request` | `503 EMAIL_UNAVAILABLE` | nada; um link pendente anterior continua valendo |
| `/email-change/confirm` (com `AUTH_EMAIL_CHANGE_NOTIFY_OLD=true`) | `503 EMAIL_UNAVAILABLE` | nada: o e-mail não muda e o mesmo link confirma depois |
| `/email-verify/request` | `503 EMAIL_UNAVAILABLE` | nada |
| `/email-recovery/request` (senha e MFA corretos) | `503 EMAIL_UNAVAILABLE` | nada |
| `/activation/request` | `202`, a mesma resposta de um endereço desconhecido | nada; o link anterior continua valendo |
| `/password-reset/request` | `202`, a mesma resposta de um endereço desconhecido | nada; o link anterior continua valendo |

O critério é o que a resposta já revela. O cadastro já responde `409`
para endereço em uso; troca e verificação de e-mail exigem login; a
recuperação de e-mail só chega ao envio depois que a senha (e o MFA)
bateram — no mesmo ponto em que um `new_email` em uso responde `409`.
Nessas rotas o `503` não conta nada que o chamador não soubesse.

!!! warning "Reset e reenvio de ativação continuam respondendo `202`"
    Essas duas rotas respondem igual para endereço cadastrado e
    desconhecido, de propósito. Se a falha de envio virasse `503`, ela só
    apareceria para contas que existem — e a rota viraria oráculo de
    enumeração. Por isso a falha é **logada em `ERROR`** (sem o endereço)
    e a resposta continua `202`. O token só é gravado **depois** que o
    e-mail saiu, então a falha não grava nada e o link pendente anterior
    não é invalidado.

!!! info "O que vai para o log"
    O `EmailUtils` loga uma vez, em `ERROR`, com `smtp_host` e
    `smtp_port` — nunca o destinatário nem a credencial. Falha de rede ou
    de TLS leva o traceback (é ali que mora o diagnóstico); recusa
    respondida pelo servidor (`550` para um destinatário, por exemplo)
    não leva, porque o texto da resposta pode citar o endereço. Pelo
    mesmo motivo, só a falha de rede ou de TLS fica encadeada em
    `__cause__`; a recusa sai `from None`, e o handler que loga todo 5xx
    não tem o que imprimir.

Ao chamar o `UserAuthService` fora do `make_auth_router`, a regra é a de
qualquer erro depois de um `flush`: faça `rollback` da sessão antes de
reutilizá-la. O roteador faz isso sozinho, porque nunca chega ao
`commit`.

### Em background (`AUTH_SEND_EMAILS_IN_BACKGROUND`)

*(v0.332.0+)*

Por padrão a rota espera o SMTP: um servidor lento deixa o cadastro e o
reset lentos. Com `AUTH_SEND_EMAILS_IN_BACKGROUND=true`, as rotas do
`make_auth_router` entregam o envio ao `BackgroundTasks` da requisição — a
resposta sai primeiro e o e-mail depois. Medido em
`tests/auth/test_email_background.py`, com um mailer que leva 2 s por envio
servido por uvicorn real: o `POST /auth/signup` respondeu depois dos 2 s no
default e antes de o envio terminar em background — é o que o teste afirma.
Numa execução com `--durations` (que conta também a subida do servidor), o
default levou 2,2 s e o background 0,18 s com a máquina ociosa; sob a suíte
em paralelo o background chegou a 0,67 s.

```bash
# .env
AUTH_SEND_EMAILS_IN_BACKGROUND=true
```

O que muda, e o preço:

- **O tempo da resposta para de depender do envio.** O
  `/password-reset/request` de um endereço cadastrado deixa de demorar mais
  que o de um desconhecido, uma diferença que dava para medir de fora.
- **Falha de SMTP não responde `503`.** A resposta já foi; a falha sai em
  `ERROR` no logger `tempest_fastapi_sdk.auth.email_delivery`, com o tipo do
  e-mail e só o **domínio** do destinatário.
- **Não há rollback por falha de envio.** O cadastro fica gravado sem o
  e-mail de ativação (o usuário pede o reenvio), e no reset e no reenvio de
  ativação o token novo já substituiu o pendente quando o envio falha — o
  link anterior para de valer e o novo não chegou. Pedir de novo resolve.
- **O e-mail só sai se a requisição deu certo.** O `make_auth_router`
  comita antes de responder, e uma exceção depois de agendar troca a
  resposta por uma de erro, que não leva as tarefas: nenhum link aponta
  para um token que não foi gravado.

Fora de uma requisição — o `UserAuthService` chamado de um comando, de uma
task ou de um teste — não há `BackgroundTasks`, e o envio é aguardado como
no default, com a mesma `EmailDeliveryException`.

!!! tip "Desligamento"
    O Starlette roda as tarefas de fundo de uma resposta antes de encerrar
    a requisição, então o desligamento gracioso do servidor espera por elas
    como espera qualquer requisição em andamento. Um `asyncio.create_task`
    solto não teria essa garantia.
