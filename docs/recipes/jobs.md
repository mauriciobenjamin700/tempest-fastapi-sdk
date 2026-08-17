# Jobs — trabalho longo com status

Uma fila entrega a chamada ao worker. Ela não responde nada do que a
pessoa na frente da tela está perguntando:

- alguém já pegou isso, ou ainda está na fila?
- está rodando **agora**?
- terminou? o que produziu?
- se parou, por quê — em português, não num traceback.

O `AsyncResultBackend` do TaskIQ chega perto, mas é chaveado por id de
task, guarda o retorno da função e não é uma tabela que a aplicação
consulta, pagina ou mostra num admin. O que a interface quer é uma
**linha**.

Este é o par simétrico do [outbox](outbox.md): lá é *mensagem a
publicar*, aqui é *trabalho a executar*.

## 1. A tabela

Subclasse `BaseJobModel` e escolha o `__tablename__` — igual ao
`BaseOutboxModel`:

```python
# src/db/models/job.py
from tempest_fastapi_sdk.tasks import BaseJobModel


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"
```

São três linhas porque o resto já vem: `kind`, `status`, `params`,
`payload`, `result_id`, `error`, `attempts`, `max_attempts`,
`started_at`, `finished_at` — mais o `id` / `is_active` / `created_at` /
`updated_at` de todo `BaseModel`.

| Coluna | Para quê |
| --- | --- |
| `kind` | que trabalho é este; é por onde o worker ramifica e a interface filtra |
| `status` | `queued` → `running` → `done` / `failed`, indexado |
| `params` | entrada pequena, em JSON |
| `payload` | entrada grande — o arquivo que o broker não deveria carregar |
| `result_id` | o registro que o trabalho produziu, para a tela linkar direto |
| `error` | por que parou, escrito para o usuário |

## 2. Enfileirar

O `JobStore` recebe o `AsyncDatabaseManager`, não uma sessão: cada
chamada abre e fecha a sua, porque quem usa isso é um handler que
enfileira, um worker que trabalha por minutos e uma tela que pergunta de
2 em 2 segundos — nenhum deles deve segurar sessão pelo caminho.

```python
# src/api/routers/extraction.py
from uuid import UUID

from fastapi import APIRouter, UploadFile

from src.db.models.job import JobModel
from src.api.dependencies.resources import db
from src.tasks import extract_document

from tempest_fastapi_sdk.tasks import JobStore

router = APIRouter(prefix="/api/extraction")
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@router.post("/")
async def start_extraction(file: UploadFile) -> dict[str, UUID]:
    """Aceita o documento e devolve o id do job para acompanhar."""
    job = await store.enqueue(
        "extract",
        params={"filename": file.filename or "sem-nome.pdf"},
        payload=await file.read(),
    )
    await extract_document.enqueue(str(job.id))
    return {"job_id": job.id}
```

A ordem é essa de propósito: **grave a linha, depois mande a task**. A
linha é o que a interface lê, e ela precisa existir antes de o worker
poder reivindicar.

## 3. O worker

```python
# src/tasks/__init__.py
from uuid import UUID, uuid4

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore, TaskQueue


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


class UnsupportedFormat(Exception):
    """O arquivo enviado não é algo que sabemos ler."""


async def read_tender(payload: bytes | None) -> UUID:
    """O trabalho de verdade; devolve o id do que produziu.

    Args:
        payload (bytes | None): O documento reivindicado com o job.

    Returns:
        UUID: O id do rascunho gerado.

    Raises:
        UnsupportedFormat: Quando o documento não é legível.
    """
    if not payload:
        raise UnsupportedFormat("arquivo vazio")
    return uuid4()


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@tq.task
async def extract_document(job_id: str) -> None:
    """Reivindica o job, faz o trabalho e fecha a linha.

    Args:
        job_id (str): O id que a rota mandou junto com a task.
    """
    job = await store.claim(UUID(job_id))
    if job is None:
        return
    try:
        draft_id = await read_tender(job.payload)
    except UnsupportedFormat as exc:
        await store.fail(job.id, f"Nao consegui ler o arquivo: {exc}")
    else:
        await store.succeed(job.id, result_id=draft_id)
```

Três coisas acontecendo aí, cada uma por um motivo:

- **`claim` é o que separa "na fila" de "rodando".** Sem ele a interface
  não distingue "o worker está ocupado" de "ninguém pegou" — que é
  exatamente a pergunta quando algo demora.
- **`claim` devolve `None` quando o job não é seu** (outro worker pegou,
  ou o id não existe). É um `UPDATE` condicional, então dois workers
  disputando o mesmo id não empatam: um vê a linha mudar, o outro não.
- **`succeed` / `fail` apagam o `payload`.** Sem isso a tabela de jobs
  terminados vira uma pilha de documentos.

!!! warning "Não segure a sessão através do trabalho"
    O `claim` já devolveu o `payload`; a partir daí o worker trabalha
    **sem sessão aberta** e só volta ao banco para fechar a linha. Uma
    transação que lê primeiro e escreve minutos depois é o caso que
    nenhum `busy_timeout` resolve — veja
    [Banco de dados](database.md#sqlite-com-um-worker-wal-e-busy-timeout).

## 4. A tela que pergunta "já terminou?"

```python
# src/ui/pages/extraction.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def show_progress(job_id: UUID) -> list[str]:
    """Acompanha o job até ele terminar.

    Args:
        job_id (UUID): O job que a tela está observando.

    Returns:
        list[str]: Cada status pelo qual o job passou.
    """
    seen: list[str] = []
    async for job in store.watch(job_id, interval=2.0):
        seen.append(job.status)
    return seen
```

`watch` rende o job a **cada mudança de status**, até um estado terminal,
e então encerra. O status atual sai imediatamente, então quem assina
depois de o job já ter terminado ainda recebe exatamente um valor.

O detalhe que este helper existe para não deixar errar: **nenhuma sessão
fica aberta entre os ticks**. Cada consulta abre e fecha a sua, então o
worker que escreve no mesmo banco nunca fica bloqueado pela tela que o
observa.

`timeout=` desiste com `TimeoutError` em vez de esperar para sempre.

## 5. O worker que morreu segurando o job

Uma linha `running` que ninguém vai fechar é a falha que a fila não
enxerga: a task morreu, a linha não. `reclaim_stale()` readmite:

```python
# src/tasks/__init__.py
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore, TaskQueue


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@tq.interval(seconds=60)
async def reclaim_jobs() -> None:
    """Devolve à fila o que um worker morto deixou em RUNNING."""
    await store.reclaim_stale()
```

Rows com `started_at` mais velho que `stale_after` voltam para `queued` —
a não ser que já tenham gasto o `max_attempts`, caso em que são fechados
como `failed`. Sem esse limite, um job que derruba o worker seria
readmitido para sempre.

!!! info "Sem `stale_after`, o método recusa"
    `JobStore(db, model=JobModel)` sem `stale_after` levanta
    `RuntimeError` no `reclaim_stale()` em vez de adivinhar um limite.

## 6. Listar

```python
# src/services/extraction.py
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStatus, JobStore


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def dashboard() -> tuple[list[JobModel], list[JobModel]]:
    """Lê o que a tela de acompanhamento mostra.

    Returns:
        tuple[list[JobModel], list[JobModel]]: Os jobs recentes e os que
        estão rodando agora.
    """
    recentes = await store.list_recent(kind="extract", limit=20)
    rodando = await store.list_recent(status=JobStatus.RUNNING)
    return recentes, rodando
```

Devolve `[]` quando nada casa — "ainda não há jobs" é uma resposta bem
sucedida, não um 404.

## 7. Cancelar

O usuário clicou em "cancelar". Nada em TaskIQ — nem em broker nenhum que
o SDK fala — oferece "mate a task com este id": uma vez rodando dentro do
processo worker, só aquele processo pode pará-la. Então o cancelamento é
**cooperativo**: o request escreve `cancelled` e responde na hora; o
worker lê esse status em pontos combinados e desiste.

```python
# src/services/extraction.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def cancelar(job_id: UUID) -> bool:
    """Pede para o job parar.

    Args:
        job_id (UUID): O job a cancelar.

    Returns:
        bool: True quando havia algo para parar.
    """
    job: JobModel | None = await store.cancel(job_id, reason="cancelado pelo usuário")
    return job is not None
```

!!! tip "Idempotente de propósito"
    `cancel()` devolve `None` — e não levanta — quando não há o que parar:
    id inexistente, job já concluído, já falho, ou já cancelado. Clicar
    duas vezes, ou clicar bem na hora em que o job terminou sozinho, não é
    erro.

### O worker desiste

`run_cancellable` é o checkpoint que roda **durante** o trabalho, não
entre etapas. Ele corre a corotina contra um predicado consultado num
intervalo, e quando o predicado diz para parar, a corotina é cancelada de
verdade — a requisição HTTP em voo é abortada e o worker fica livre dentro
do intervalo, em vez de terminar uma chamada cujo resultado ninguém quer.

```python
# src/tasks/extract.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    BaseJobModel,
    JobStore,
    StageInterruptedError,
    run_cancellable,
)


class JobModel(BaseJobModel):
    """Uma unidade de trabalho longo desta aplicação."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def resumir(texto: str) -> str:
    """Trabalho longo de verdade (chamada de rede, cancelável).

    Args:
        texto (str): O texto a resumir.

    Returns:
        str: O resumo.
    """
    return texto[:100]


async def executar(job_id: UUID) -> None:
    """Roda o job, desistindo se ele for cancelado no meio.

    Args:
        job_id (UUID): O job a executar.
    """
    job: JobModel | None = await store.claim(job_id)
    if job is None:
        return

    try:
        resumo: str = await run_cancellable(
            resumir("um texto longo"),
            interrupted=store.cancellation_watch(job_id),
        )
    except StageInterruptedError:
        return

    await store.succeed(job_id)
    print(resumo)
```

!!! danger "Só funciona em await cancelável de verdade"
    Trabalho entregue a `asyncio.to_thread` **não** é cancelável: cancelar
    a corotina abandona o wrapper enquanto a thread segue até o fim,
    ainda ocupando a CPU e ainda competindo com o próximo job. Para essa
    forma — inferência local, por exemplo — cheque entre as etapas, e
    cheque de novo antes de gravar o resultado.

!!! info "`succeed` recusa por cima de um cancelamento"
    O worker que passou reto do último checkpoint ainda não sobrescreve a
    linha: `succeed()`/`fail()` levantam `JobCancelledError`, subclasse de
    `JobAlreadyFinishedError`. Os dois casos são diferentes de propósito —
    `JobAlreadyFinishedError` puro diz que dois workers acham que o job é
    deles, e este diz que o sistema fez exatamente o que mandaram. Logue e
    siga; não alerte.

!!! warning "`cancelled` é terminal, mas não é falha"
    Entra em `TERMINAL_JOB_STATUSES` (o polling para, o `payload` some),
    mas nada deu errado. Uma tela que destaca `failed` deve deixar este em
    paz, e um alerta que dispara em falha não deve tocar.

## Erros

| Exceção | Quando |
| --- | --- |
| `JobNotFoundError` | o id não existe (`get`, `succeed`, `fail`, `watch`) |
| `JobAlreadyFinishedError` | fechar um job que já é terminal — dois workers acham que o job é deles |
| `JobCancelledError` | fechar um job que o usuário cancelou no meio; subclasse da anterior, para o worker distinguir "fizemos o que mandaram" de "a concorrência está errada" |
| `StageInterruptedError` | `run_cancellable` viu o cancelamento; não é falha, o handler só retorna |

São `LookupError` / `RuntimeError`, não `AppException`: o store roda no
worker tanto quanto num request, e worker não tem status HTTP para
responder. Traduza na borda com
[`not_found_exception(...)`](openapi-errors.md#a-fabrica-not_found_exception-conflict_exception).

**Recap:** subclasse `BaseJobModel` e ganhe a tabela; `enqueue` grava a
linha antes de a task sair; `claim` separa "na fila" de "rodando" e é
seguro sob disputa; `succeed`/`fail` fecham e apagam o `payload`;
`watch` é o polling sem sessão pendurada; `reclaim_stale` devolve o que
um worker morto deixou preso; `cancel` + `run_cancellable` param o que
está rodando, de forma cooperativa, porque não existe outra.
