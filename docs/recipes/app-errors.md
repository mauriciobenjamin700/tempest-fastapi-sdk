# Erros do app: quando o cliente quebra na mão do usuário

Seu app mobile estourou um `TypeError` na tela de pagamento. O usuário fecha
o app e vai embora. Você fica sabendo por um print no WhatsApp, três dias
depois, sem versão, sem aparelho, sem stack trace.

Esta receita monta o lugar onde esse relato cai sozinho.

## O que você vai construir

Um endpoint público que aceita o relato, uma tabela onde ele fica
consultável, e uma listagem filtrável para investigar.

```python
from tempest_fastapi_sdk.app_errors import (
    AppErrorService,
    make_app_error_model,
    make_app_error_router,
)
```

## A tabela

O SDK traz a linha abstrata; o seu projeto cria a tabela concreta, porque o
nome da tabela de usuários é do seu projeto:

```python
# src/db/models/app_error.py
from tempest_fastapi_sdk.app_errors import make_app_error_model

AppErrorModel = make_app_error_model(
    user_table="users",
    tablename="app_errors",
)
```

Três decisões vêm prontas aí dentro, e cada uma existe porque a alternativa
falha em silêncio:

!!! info "`user_id` é nullable, e a FK é `SET NULL`"
    Erro no fluxo de login acontece **antes** de existir usuário
    autenticado — e é justo o mais difícil de depurar pelo app. Exigir
    usuário derrubaria o caso mais valioso.

    E a FK usa `ON DELETE SET NULL`, não o `CASCADE` do resto do schema: o
    relato descreve defeito da **aplicação**, não do usuário. Apagar a conta
    não pode apagar a evidência do bug.

!!! tip "`created_at` tem índice próprio"
    A leitura padrão é "mais recente primeiro", paginada, e esta é a tabela
    que cresce sem limite natural. Sem o índice, cada página custa um sort
    da tabela inteira.

## Recebendo o relato

```python
# src/api/app.py
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.app_errors import AppErrorService, make_app_error_router

from src.db.models.app_error import AppErrorModel
from src.db.session import get_session


def service_factory(session: AsyncSession) -> AppErrorService:
    """Build the service for one request.

    Args:
        session (AsyncSession): The request-scoped session.

    Returns:
        AppErrorService: The service.
    """
    return AppErrorService(BaseRepository(session, model=AppErrorModel))


async def session_factory() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session.

    Yields:
        AsyncSession: The session.
    """
    async for session in get_session():
        yield session


app: FastAPI = FastAPI()
app.include_router(
    make_app_error_router(
        service_factory=service_factory,
        session_factory=session_factory,
    )
)
```

O app manda:

```json
{
  "code": "PLAN_ACTIVATION_FAILED",
  "message": "TypeError: null is not an object (evaluating 'user.id')",
  "platform": "ios",
  "os_version": "18.2",
  "app_version": "1.4.2+310",
  "device_model": "iPhone 15 Pro"
}
```

Só `code` e `message` são obrigatórios. Todo o resto é o que o app
**conseguiu** coletar no momento da falha — exigir qualquer um deles
transformaria uma coleta incompleta num relato perdido.

## As duas regras que fazem isso funcionar

### Um relato truncado vale mais que um relato perdido

Payload acima do limite da coluna é **cortado**, nunca recusado:

```python
from tempest_fastapi_sdk.app_errors import (
    APP_ERROR_TRUNCATION_SUFFIX,
    AppErrorService,
)


def exemplo() -> str | None:
    """Show what a value over the limit becomes.

    Returns:
        str | None: The cut value, marked.
    """
    return AppErrorService.truncate("x" * 5000, 4000)
```

O valor volta com `…[truncado]` no fim, para quem lê a listagem saber que
falta conteúdo.

!!! danger "Por que não 422"
    Quem está mandando é um app que **acabou de quebrar**. Ele não tem
    caminho de tratamento para uma recusa: o 422 vira exceção dentro do
    handler de exceção, e o relato simplesmente evapora. Recusar por tamanho
    é perder exatamente o relato mais interessante — o que veio com stack
    trace grande.

### `user_id` vem do token, nunca do corpo

`AppErrorReportSchema` — o que o cliente pode mandar — **não tem** o campo.
Quem o preenche é o serviço, a partir de quem a requisição autenticou:

```python
from uuid import UUID

from tempest_fastapi_sdk.app_errors import AppErrorReportSchema, AppErrorService


async def registrar(
    service: AppErrorService, data: AppErrorReportSchema, caller: UUID | None
) -> None:
    """Store a report attributed to the authenticated caller.

    Args:
        service (AppErrorService): The service.
        data (AppErrorReportSchema): The body the client sent.
        caller (UUID | None): Who the token says is calling.
    """
    await service.report_error(data, user_id=caller)
```

!!! warning "É por isso que são dois schemas"
    Se o `user_id` fosse campo do corpo, qualquer cliente poderia atribuir o
    próprio erro à conta de outra pessoa. A separação entre
    `AppErrorReportSchema` e `AppErrorCreateSchema` não é organização: é o
    que torna isso impossível, em vez de apenas desencorajado.

## Investigando

A listagem é **opt-in**. Ela só existe se você passar a dependência de
admin:

```python
from fastapi import APIRouter

from tempest_fastapi_sdk.app_errors import make_app_error_router

from src.api.dependencies.auth import require_admin
from src.api.factories import service_factory, session_factory

router: APIRouter = make_app_error_router(
    service_factory=service_factory,
    session_factory=session_factory,
    admin_dependency=require_admin,
)
```

Sem `admin_dependency`, a rota `GET` **não é montada** — e isso é
deliberado: a listagem devolve stack trace e identificador de aparelho, que
não podem ficar abertos por esquecimento de proteger.

!!! tip "Ou nem monte o endpoint"
    Se o seu serviço já usa o `AdminSite`, registre a tabela lá: listagem,
    filtro e paginação saem de graça, e você não mantém rota nenhuma. O
    `GET` do router é para quem tem painel próprio.

O corte que resolve a maioria das investigações é `code` + `app_version` —
isola um defeito específico numa build específica:

```text
GET /api/app-errors?code=PLAN_ACTIVATION_FAILED&app_version=1.4.2
```

!!! info "O intervalo de datas é semiaberto, de propósito"
    O filtro monta `created_at >= start` e `created_at < end + 1 dia`, em
    vez de `func.date(created_at)`. Aplicar função na coluna **descarta o
    índice** — e esta é a tabela que mais cresce. Para você, `start_date` e
    `end_date` continuam inclusivos nos dois lados.

!!! warning "`start_date` e `end_date` são dias **UTC**"
    `created_at` é gravado por `utcnow`, e o filtro compara a data que você
    manda com meia-noite — então o corte é um limite UTC, não o do fuso onde
    o processo roda.

    Medido: um relato gravado em `2026-03-10T02:30Z` — que no relógio de
    Brasília ainda é `2026-03-09 23:30` — aparece filtrando `2026-03-10`
    (`total=1`) e **não** aparece filtrando `2026-03-09` (`total=0`). A
    resposta é a mesma com o servidor em `America/Sao_Paulo` ou em
    `Asia/Tokyo`, que é o ponto: a janela não muda de sentido conforme a
    máquina.

    Consequência prática: monte o intervalo a partir de
    `datetime.now(UTC).date()`, não de `datetime.now().date()`. Em BRT, as
    duas divergem por três horas todo dia, e é nelas que "hoje" devolve
    vazio.

## O teto de requisições

O `POST` é público. Ele precisa de teto, e o teto **não mora neste módulo**:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    FailOpenRateLimitStore,
    MemoryRateLimitStore,
    RateLimitMiddleware,
)

app: FastAPI = FastAPI()
app.add_middleware(
    RateLimitMiddleware,
    store=FailOpenRateLimitStore(MemoryRateLimitStore()),
    max_requests=120,
    window_seconds=3600.0,
)
```

!!! danger "O `FailOpenRateLimitStore` não é enfeite"
    Medido: com uma store cujo `hit` levanta, a exceção **propaga** pelo
    `RateLimitMiddleware` e o chamador recebe erro. Para a maioria dos
    endpoints isso se defende — limitador que não consegue contar não
    consegue proteger.

    Aqui é o contrário. O momento em que a store de contadores está mal é
    exatamente o momento em que os erros disparam, e recusar os relatos
    então destrói a evidência do incidente que está sendo reportado. Perder
    o relato é pior que servir acima do teto.

    O wrapper torna essa troca explícita, e ela fica visível: cada falha vai
    para o log em `WARNING`, então "o teto não está sendo aplicado" não
    acontece em silêncio.

## Recapitulando

- `make_app_error_model` cria a tabela; `user_id` nullable e `SET NULL`
  preservam o relato de erro de login e a evidência do bug.
- Só `code` e `message` são obrigatórios; o resto é o que o app conseguiu
  coletar.
- Valor grande é truncado com marcador, nunca recusado.
- `user_id` vem do token — o schema do cliente não tem o campo.
- A listagem só existe com `admin_dependency`, ou pelo `AdminSite`.
- O teto do `POST` é `RateLimitMiddleware`, e precisa falhar aberto.
