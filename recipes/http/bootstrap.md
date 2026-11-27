# Bootstrap da aplicação e envelope de erro

Use para criar a app com `create_app`, ter liveness/readiness, pôr todo 4xx e o 422 no envelope de erro, notificar falhas e localizar mensagens.

Parte da receita [Camada HTTP](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http.md).

[A seção 2 do tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/tutorial.md#2-settings-server-factory-do-app-entrypoint) mostra o `create_app()` mínimo. Esta receita é a versão **estendida**, conectando tudo que `tempest_fastapi_sdk.api` entrega — exception handlers, CORS, middleware de request-ID, o health router com checks extras, uma dependência de token de segredo compartilhado e um manager extra de Redis — tudo a partir da mesma localização canônica `src/api/app.py`. O padrão de bootstrap continua idêntico; só o conteúdo de `create_app()` cresce.

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    apply_cors,
    configure_logging,
    make_health_router,
    make_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings


configure_logging(level=settings.LOG_LEVEL, json_output=settings.LOG_JSON)

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
)
redis = AsyncRedisManager(settings.REDIS_URL)
require_token = make_token_dependency(settings.TOKEN_SECRET)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await db.connect()
    await redis.connect()
    try:
        yield
    finally:
        await redis.disconnect()
        await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(
        title="my-service",
        version=settings.VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, settings)
    register_exception_handlers(app)

    # Meta endpoints at the root prefix.
    app.include_router(
        make_health_router(
            db=db,
            checks={"redis": redis.health_check},
            version=settings.VERSION,
        ),
    )

    # Business endpoints under /api/<domain>, guarded by the shared secret.
    from src.api.routers import users

    app.include_router(
        users.router,
        prefix="/api",
        dependencies=[Depends(require_token)],
    )
    return app


app = create_app()
```

Pontos-chave:

- `src/server.py` e `main.py` (one-liner) ficam exatamente como na [seção 2 do tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/tutorial.md#2-settings-server-factory-do-app-entrypoint) — só `create_app()` muda quando você adiciona primitivos. Nunca inicie o uvicorn via `subprocess.run(["uvicorn", ...])`; sempre importe `app` de `src.api.app` ou chame `uvicorn.run("src.api.app:app", ...)` programaticamente de `src/server.py`.
- `RequestIDMiddleware` lê/escreve `X-Request-ID` e semeia `request_id_ctx` para que toda linha de log emitida durante a requisição carregue o ID de correlação.
- `apply_cors(app, settings)` lê os defaults de `CORSSettings`; passe overrides nomeados para mudanças pontuais.
- `register_exception_handlers(app)` conecta três handlers, cada um com seu nível de log (mais um quarto, para o 422, quando `envelope_validation_errors=True` — veja [O 422 dentro do envelope](#o-422-dentro-do-envelope)):
    - `AppException` → envelope `{detail, code, details}` + log `INFO` (4xx) ou `ERROR` + traceback + `500.log` (5xx).
    - `HTTPException` → mantém o body padrão do Starlette (`{"detail"}`) em 4xx com log `INFO`; em 5xx aplica o envelope SDK + traceback + `500.log`.
    - `Exception` (catch-all) → envelope SDK + traceback + `500.log` (corrige o default do Starlette, que devolve só `"Internal Server Error"` sem log).

    Todos os handlers respeitam `RequestIDMiddleware`: a linha de log carrega o `request_id`, e o envelope expõe ele em `details` para correlacionar com o cliente. Passe `log_traceback=False` se um APM (Sentry, OpenTelemetry) já estiver capturando a trace.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` monta `GET /health/liveness` e `GET /health/readiness` (retorna `503` quando algum check falha) no prefixo raiz — veja [Liveness e readiness](#liveness-e-readiness).
- `make_token_dependency(secret)` retorna uma dependência async que valida `X-Token` via `hmac.compare_digest`; segredo vazio levanta `ValueError` ao montar a dependência — para rodar local sem gate, passe `allow_unauthenticated=True`. A dependência vive ao lado do resto da cola de auth em `src/api/dependencies/auth.py` quando crescer além do one-liner acima.

## Liveness e readiness

São duas perguntas diferentes, feitas por quem decide coisas diferentes:

- **Liveness** — *"o processo está vivo?"*. Se falha, o orquestrador
  (Kubernetes, ECS) **reinicia** o container. Por isso `GET /health/liveness`
  não toca dependência nenhuma: um Redis fora do ar não se resolve reiniciando
  a API, e um liveness que dependesse dele derrubaria todas as réplicas ao
  mesmo tempo, em loop.
- **Readiness** — *"posso receber tráfego agora?"*. Se falha, o balanceador
  **tira a réplica da rotação** e devolve quando voltar a passar. É aqui que
  entram banco, cache e broker: `GET /health/readiness` roda cada check e
  responde `503` quando algum falha.

Um único `GET /health` mistura as duas coisas — ou reinicia por causa de uma
dependência, ou manda tráfego para quem não consegue atendê-lo. Ao pedir o
health de um serviço novo, peça os dois endpoints.

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import make_health_router


async def redis_check() -> bool:
    """Responde na hora: dependência saudável."""
    return True


async def search_check() -> bool:
    """Pendura, como um socket meio aberto que nunca responde."""
    await asyncio.sleep(60)
    return True


app = FastAPI()
app.include_router(
    make_health_router(
        checks={"redis": redis_check, "search": search_check},
        timeout=0.5,
    ),
)


async def main() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        liveness = await client.get("/health/liveness")
        readiness = await client.get("/health/readiness")
    print(liveness.status_code, liveness.json())
    print(readiness.status_code, readiness.json())


asyncio.run(main())
```

Saída (o processo inteiro levou 1,7 s, apesar do `sleep(60)`):

```text
Health check 'search' timed out after 0.5s
200 {'status': 'ok'}
503 {'status': 'not_ready', 'checks': {'redis': True, 'search': False}}
```

O que acontece na readiness:

- **Os checks rodam em paralelo**, cada um limitado por `timeout` (default
  `3.0` segundos; `None` desliga). O que estoura conta como falha **só para
  ele** — o `redis` acima continua `True` —, e a resposta sai logo depois do
  `timeout`, não quando o probe do orquestrador desiste. Mantenha o `timeout`
  abaixo do timeout do probe.
- **O log não leva a mensagem da exceção.** Um check que levanta gera
  `Health check 'database' raised ConnectionRefusedError` — nome e tipo, nunca
  o texto, porque mensagem de driver de banco ou de broker costuma carregar a
  DSN com usuário e senha.

!!! warning "Cancelamento só acontece num `await`"
    O `timeout` cancela o check no próximo ponto de `await`. Um check que
    bloqueia o event loop com I/O síncrono (um driver não-async, um
    `time.sleep`) não pode ser interrompido — e trava o processo inteiro, não
    só a readiness. Rode esse tipo de chamada com `asyncio.to_thread`.

!!! tip "Fila"
    `MessageBroker.health_check` faz o `ping` real do FastStream, com
    `timeout=2.0` por default — abaixo dos 3 s do router. Com o RabbitMQ
    parado depois do boot, ele devolve `False`:
    `make_health_router(checks={"queue": mq.health_check})`.


## Todo 4xx dentro do envelope

O 422 era o furo mais visível, não o único. As outras quatro categorias de
erro de cliente **passam pelo handler do SDK** — seis casos, todos medidos
na tabela abaixo — e respondiam o corpo nu do Starlette, sem `code` e em inglês, mesmo num serviço
com `MessageCatalog` configurado:

| Caminho | Antes | Com `envelope_client_errors=True` |
| --- | --- | --- |
| `raise HTTPException(404, "pedido 42 não existe")` | `{"detail": "pedido 42 não existe"}` | mesma mensagem **+** `code: NOT_FOUND` |
| `raise HTTPException(403)` | `{"detail": "Forbidden"}` | `Acesso negado` + `code: FORBIDDEN` |
| `raise HTTPException(409)` | `{"detail": "Conflict"}` | `Conflito de recurso` + `code: CONFLICT` |
| `Depends(HTTPBearer())` sem token | `{"detail": "Not authenticated"}` | mesma mensagem **+** `code: UNAUTHORIZED` |
| rota inexistente | `{"detail": "Not Found"}` | `Recurso não encontrado` + `code: NOT_FOUND` |
| método errado | `{"detail": "Method Not Allowed"}` | mesma mensagem + `code: HTTP_405` |

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    """Monta a aplicação.

    Returns:
        FastAPI: A aplicação configurada.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_client_errors=True,       # ← todo 4xx, o 422 incluído
    )
    return app
```

!!! danger "Mensagem que você escreveu **não** é traduzida"
    Essa é a linha que separa "localizar" de "perder informação". O
    Starlette preenche `detail` com a *phrase* do status quando o caller não
    passa uma, então os dois casos são distinguíveis:

    - `HTTPException(404, "pedido 42 não existe")` → mensagem sua, **preservada**
    - `HTTPException(404)` → `detail == "Not Found"`, que é a phrase → traduzida

    Trocar a primeira pelo genérico do catálogo destruiria a mensagem mais
    informativa das duas. O `"Not authenticated"` do `HTTPBearer` cai no
    primeiro grupo também — medido — e é preservado.

!!! info "Por que dois flags e não um renomeado"
    `envelope_validation_errors` shippou na v0.284.0 cobrindo só o 422.
    `envelope_client_errors` é o **superconjunto**: liga todo 4xx, o 422
    incluído. Quem já tinha ligado o primeiro não muda de comportamento ao
    atualizar — que é exatamente o que um rename silencioso faria.

    Precisa de tudo? Só `envelope_client_errors=True`. Precisa só do 422?
    Só o outro.

!!! note "`HTTP_<status>` para status que o SDK não modela"
    O mapa status→`code` é **declarado**, não derivado: medido, treze classes
    de `AppException` carregam um code de 401 e quatro de 404, então não há
    status de onde ler um code único.

    Status fora do mapa responde `HTTP_<status>` e mantém a phrase do
    framework. 400 e 405 estão nesse grupo hoje — nenhuma subclasse de
    `AppException` carrega code para eles. O cliente ainda parseia um
    formato só; o que falta é a tradução, não o `code`.

## O 422 dentro do envelope

`register_exception_handlers` conecta três handlers, e o 422 não passa por
nenhum: o FastAPI intercepta `RequestValidationError` no handler default
dele. Então o erro que o cliente vê mais era o único fora do modelo de erro
do SDK — sem `code`, e sem passar pelo `MessageCatalog` que o serviço
configurou.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    """Monta a aplicação.

    Returns:
        FastAPI: A aplicação configurada.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_validation_errors=True,        # ← liga o 422 no envelope
    )
    return app
```

Com o flag ligado, `POST /login` com `password` curta responde:

```json
{
  "detail": "Erro de validação",
  "code": "VALIDATION_ERROR",
  "details": {
    "errors": [
      {
        "loc": ["body", "password"],
        "type": "string_too_short",
        "msg": "O texto deve ter no mínimo 12 caractere(s)"
      }
    ]
  }
}
```

!!! danger "O 422 default devolve o valor submetido — a senha inclusive"
    O corpo que o FastAPI gera inclui `input` para cada campo que falhou.
    Medido, com `password` reprovando em `min_length`:

    ```json
    {"detail": [{"type": "string_too_short", "loc": ["body", "password"],
                 "msg": "String should have at least 12 characters",
                 "input": "hunter2"}]}
    ```

    E `SecretStr` **não** protege: a validação roda antes de o wrapper de
    segredo existir, então um campo `SecretStr` que reprova também devolve
    o valor cru. Medido nos dois casos.

    Esse é o motivo mais forte para ligar o flag. `details` carrega `loc` e
    `type`, e **nunca** `input` — não é um aviso para você implementar, é
    uma linha que o handler não escreve.

!!! info "A tabela é por tipo de erro, não por campo"
    As mensagens saem do catálogo com a chave
    `VALIDATION.<tipo do pydantic>` — `string_too_short`, `missing`,
    `int_parsing` e as outras 101. Isso é vocabulário do pydantic, igual em
    todo serviço, então **campo novo num schema nasce traduzido**: não há
    tabela por campo para manter.

    São 104 tipos, o conjunto exato do `pydantic_core.ErrorType`, e o
    `tests/test_pydantic_error_types_guard.py` falha quando o pydantic
    instalado e a tabela discordam em qualquer direção.

    O catálogo carrega **só português**, de propósito: o `msg` do próprio
    pydantic já *é* a mensagem em inglês, então o handler cai nela e o SDK
    não mantém uma cópia que pode divergir do upstream. Tipo que o catálogo
    não conhece também cai no `msg` — degrada para inglês, nunca para
    branco.

!!! info "Os validadores BR do SDK localizam a frase inteira"
    O pydantic arquiva **tudo** que um validador de campo levanta sob o tipo
    único `value_error`, com a exceção em `ctx["error"]`. Como o catálogo
    localiza por tipo, `CPFOrCNPJField`, `PhoneBRField`, `UFField`, CEP e
    chave PIX chegavam pela metade:

    ```text
    Valor inválido: invalid CPF/CNPJ
    ```

    Desde a **v0.290.0** esses validadores levantam `ValidationValueError`,
    que carrega um **código** (`INVALID_CPF_CNPJ`, `INVALID_CEP`,
    `INVALID_UF`, …) resolvido como `VALIDATION.<código>` nas duas línguas —
    `CPF ou CNPJ inválido` em pt-BR, `Invalid CPF or CNPJ` em en-US. Aqui o
    inglês **é** carregado, ao contrário da tabela do pydantic: o `msg` do
    upstream para esse caso é `Value error, invalid CPF/CNPJ`, prefixado e em
    minúscula.

    O seu validador não muda: um `ValueError` comum continua caindo em
    `VALIDATION.value_error`, e `ValidationValueError` é subclasse de
    `ValueError`, então todo `except ValueError` segue pegando.

!!! check "Placeholder nunca chega ao cliente"
    Template cujo parâmetro não veio no `ctx` não é mais mandado cru. O
    caso real: a checagem de e-mail do pydantic (`EmailStr`) reporta
    `value_error` com `ctx={"reason": ...}`, sem `error`, e o cliente
    recebia literalmente `Valor inválido: {error}`. Agora a mensagem cai no
    genérico da mesma língua, `VALIDATION.INVALID_VALUE`:

    ```json
    {"loc": ["body", "email"], "type": "value_error", "msg": "Valor inválido"}
    ```

    Por baixo, `MessageCatalog.resolve` devolve `None` quando falta
    parâmetro (antes devolvia o template com as chaves).
    `MessageCatalog.template(key, locale)` lê a string crua.

!!! warning "Ligar o flag muda o corpo, então é opt-in"
    Frontend que faz `body.detail.map(...)` e cliente gerado do OpenAPI
    modelam o 422 como array. Por isso o default é `False`, e ligar é uma
    decisão do serviço.

    O schema acompanha: com o flag ligado, o componente
    `HTTPValidationError` é reescrito para o envelope (`detail` como
    string, `code`, `details`) e o item perde `input`. Todas as rotas
    referenciam esse componente, então a doc e o runtime concordam nas duas
    posições do flag — sem isso, o schema mentiria exatamente na resposta
    que o cliente gerado mais usa.

## Avisando alguém, e escolhendo para onde o registro vai

`log_level` decide a **severidade** do 5xx. Duas coisas que ele não decide:
quem fica sabendo, e em qual configuração de logging o registro cai.

```python
# src/api/app.py

import logging

from fastapi import FastAPI, Request

from tempest_fastapi_sdk import EmailUtils, LogUtils, register_exception_handlers

from src.core.settings import settings

log: LogUtils = LogUtils(__name__, scope="logger")


async def alert_ops(request: Request, exc: Exception) -> None:
    """Avisa a operação de um 5xx, fora do caminho da resposta.

    Args:
        request (Request): A requisição que falhou.
        exc (Exception): A exceção relatada.
    """
    mailer: EmailUtils = EmailUtils(**settings.email_kwargs())
    await mailer.send(
        to=[settings.OPS_EMAIL],
        subject=f"5xx em {request.url.path}",
        body=f"{type(exc).__name__}: {exc}",
    )


def create_app() -> FastAPI:
    """Monta a aplicação.

    Returns:
        FastAPI: A aplicação configurada.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        logger=log.logger,                # ← onde o registro cai
        on_server_error=alert_ops,        # ← quem fica sabendo
        log_level=logging.ERROR,
    )
    return app
```

!!! info "`on_server_error` roda como background task"
    Chamado com `(request, exc)` **depois** de a resposta estar montada,
    então nunca atrasa nem altera o que o cliente recebe. Dispara nos
    **três** caminhos que produzem 5xx — o catch-all, `HTTPException(500)`
    e `AppException` de 5xx —, e **não** dispara em 4xx: alerta que
    dispara em 404 é alerta que ninguém lê.

!!! warning "Exceção dentro do callback é logada e engolida"
    De propósito. Medido sem essa proteção: a resposta 500 sai correta,
    mas a exceção do notificador **propaga pela pilha ASGI** e passa a ser
    o que o servidor relata — o 500 do cliente vira um 500 pior, sem
    rastro da causa real. Com a proteção, o que sobe é a exceção original.

!!! tip "`logger=` importa quando o logging é de escopo isolado"
    Os handlers logam no logger **deles**
    (`tempest_fastapi_sdk.api.handlers`). Com
    `LogUtils(..., scope="root")` — o default — isso já cai no `500.log` e
    no `error.log` do serviço; medido, 1 linha em cada.

    Com `scope="logger"`, que não configura a raiz, o logger do SDK fica
    fora da árvore configurada e o registro não chega a **nenhum** dos
    arquivos — medido, 0 e 0. Passar `logger=log.logger` fecha isso: 1 e 1.

    O tipo é `logging.Logger`, e não o protocolo `RetryLogger`, porque os
    handlers passam `extra=` e `exc_info=` e o protocolo não tem nenhum dos
    dois. Medido: `LogUtils.error(extra=...)` guarda um campo *chamado*
    `extra`, e `logging.Logger.error(request_id=...)` levanta `TypeError` —
    nenhum tipo único cobre os dois. `LogUtils` expõe `.logger`, que é o
    objeto que serve.

## Mensagens de erro localizadas (i18n)

Por padrão o `detail` do envelope é a mensagem literal da exceção (em inglês nos built-ins). Para devolver a mensagem **no idioma do cliente** sem traduzir em cada `raise`, passe um `MessageCatalog` para `register_exception_handlers`:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(...)
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),                   # ← PT-BR + EN-US embutidos
        default_locale="pt-BR",
    )
    ...
```

O handler negocia o locale a partir do header `Accept-Language` (ordenado por `q`), cai em `default_locale` quando nada casa, e resolve a **chave** da exceção — `message_key` se definido, senão o `code` — contra o catálogo. Sem catálogo, ou quando a chave não existe, mantém o `detail` literal (zero quebra de compatibilidade).

```python
# Mesmo NotFoundException, idioma decidido pelo Accept-Language do cliente:
#   Accept-Language: pt-BR  →  {"detail": "Recurso não encontrado", "code": "NOT_FOUND"}
#   Accept-Language: en-US  →  {"detail": "Resource not found",     "code": "NOT_FOUND"}
```

Para códigos de domínio (e mensagens com parâmetros), estenda o catálogo com `merge` e passe `message_params` no `raise`:

```python
# src/core/i18n.py
from tempest_fastapi_sdk import MessageCatalog, default_message_catalog

CATALOG: MessageCatalog = default_message_catalog().merge(
    {
        "pt-BR": {"USER_NOT_FOUND": "Usuário {email} não encontrado"},
        "en-US": {"USER_NOT_FOUND": "User {email} not found"},
    }
)
```

```python
# src/services/user.py
from tempest_fastapi_sdk import NotFoundException


def require_user(email: str) -> None:
    """Raise a localized 404 carrying the offending e-mail.

    Args:
        email (str): The e-mail that was not found.

    Raises:
        NotFoundException: Always — keyed to ``USER_NOT_FOUND`` so the
            handler localizes it from the request locale.
    """
    raise NotFoundException(
        "User not found",                                    # fallback literal
        code="USER_NOT_FOUND",
        message_params={"email": email},
    )
```

!!! tip "A chave segue o `code` por padrão"
    Você raramente passa `message_key` — ele cai no `code` da exceção. Defina `message_key` só quando quiser desacoplar a string traduzida do código de erro. Um template que referencia um parâmetro ausente volta sem interpolar, em vez de estourar.
