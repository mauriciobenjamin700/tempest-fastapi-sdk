# Workers, healthcheck e scheduler em produção

Use para rodar worker e scheduler em produção, checar se estão vivos e agendar no lifespan com uma réplica só.

Parte da receita [Fila e Tarefas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/queue-tasks.md).

## Workers em produção

O worker e o scheduler são processos separados apontando pros objetos crus expostos pelo `TaskQueue`. A CLI do TaskIQ resolve `módulo:atributo` com um `getattr` simples, então **exponha os dois como nomes de módulo** — um caminho com ponto não é resolvido:

```python
# src/tasks.py — depois de registrar as tarefas
from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")

broker = tq.broker
scheduler = tq.scheduler
```

```bash
# consome e executa as tarefas
taskiq worker    src.tasks:broker

# um único processo de scheduler pro cluster inteiro
taskiq scheduler src.tasks:scheduler
```

`tq.broker` é o broker TaskIQ (conhece todas as tarefas registradas); `tq.scheduler` é o `TaskiqScheduler` interno. Os dois já saem tipados (`AsyncBroker` e `TaskiqScheduler`), então `broker = tq.broker` e `scheduler = tq.scheduler` dispensam anotação — e você não precisa importar nada do `taskiq` para isso.

!!! warning "`src.tasks:tq.broker` não funciona"
    A CLI faz `getattr(module, "tq.broker")` e levanta
    `AttributeError: module 'src.tasks' has no attribute 'tq.broker'` —
    o processo nem sobe. Vale para qualquer caminho com ponto
    (`tq.scheduler`, `scheduler.scheduler`). Daí o `broker = tq.broker`
    acima.

## Healthcheck do worker e do scheduler

O healthcheck que o `TaskQueue` já tinha (`health_check`) só pergunta se o
broker está de pé. Um worker travado numa tarefa que nunca termina, ou um
scheduler cujo loop parou, continuam com o broker perfeitamente alcançável — e
o container segue `healthy`. O **heartbeat** fecha essa lacuna: o processo
prova que está fazendo o trabalho reescrevendo um arquivo com a hora atual, e
o healthcheck pergunta quantos segundos esse arquivo tem.

Ligue pelo ambiente — `from_settings` chama `enable_heartbeat` quando um dos
dois arquivos está preenchido:

```python
# src/tasks.py
from tempest_fastapi_sdk.settings import TaskIQSettings
from tempest_fastapi_sdk.tasks import TaskQueue


class Settings(TaskIQSettings):
    """Configuração do worker e do scheduler."""


tq: TaskQueue = TaskQueue.from_settings(Settings())

broker = tq.broker
scheduler = tq.scheduler
```

| Variável | Padrão | O que decide |
| --- | --- | --- |
| `TASKIQ_WORKER_HEARTBEAT_FILE` | vazio | Arquivo que cada processo de **worker** reescreve a cada período. |
| `TASKIQ_SCHEDULER_HEARTBEAT_FILE` | vazio | Arquivo que o **scheduler** reescreve a cada período, de dentro do loop. |
| `TASKIQ_HEARTBEAT_SECONDS` | `30` | Período do heartbeat. |

Os dois vazios (o default) deixam tudo como era: nenhum loop extra, nenhum
arquivo. Cada processo só escreve o arquivo que é dele, então dá para pôr as
duas variáveis nos dois containers (como o compose abaixo faz).

Ou explícito, sem settings:

```python
from datetime import timedelta

from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.redis("redis://localhost:6379/0")
tq.enable_heartbeat(
    worker_file="/tmp/worker.heartbeat",
    scheduler_file="/tmp/scheduler.heartbeat",
    every=timedelta(seconds=30),
)

broker = tq.broker
scheduler = tq.scheduler
```

### O que cada arquivo prova

- **Worker** — cada processo de worker sobe, no evento de startup do
  TaskIQ, uma task asyncio que reescreve o arquivo a cada `every`
  (`HeartbeatLoop`), e a cancela no shutdown. A batida é **local**: não
  passa pelo broker nem pelo scheduler, então cada réplica mantém o próprio
  arquivo fresco, seja qual for a réplica que o broker escolhe para cada
  tarefa. Arquivo fresco prova que o processo está vivo e que o event loop
  dele não está travado — uma tarefa que bloqueia o loop com trabalho
  síncrono para a batida. Uma tarefa que espera para sempre sem bloquear o
  loop, não: isso é caso de timeout de tarefa.
- **Scheduler** — o loop do TaskIQ acorda a cada segundo; quando o
  heartbeat vence, ele chama o `pre_send` da fonte de agenda que o
  `enable_heartbeat` pendura no `tq.scheduler`. O `pre_send` grava o arquivo
  e **cancela** o envio (`ScheduledTaskCancelledError`): nada é publicado no
  broker. Arquivo fresco prova que o loop gira — não que o scheduler ainda
  consegue publicar.

Medido com `taskiq worker` + `taskiq scheduler` reais sobre Redis, em
Compose, com `TASKIQ_HEARTBEAT_SECONDS=5` e **duas réplicas** de worker: cada
réplica e o scheduler reescreveram o seu arquivo a cada 5 s; o stream
`taskiq` no Redis ficou com `XLEN 0` (o heartbeat não publica nada); com o
scheduler parado, as duas réplicas seguiram `healthy` por mais de 100 s.

!!! note "Com `--workers N`, o arquivo prova pelo menos um processo"
    O `taskiq worker` sobe vários processos filhos (`--workers`), e cada um
    tem o próprio loop de heartbeat escrevendo no mesmo arquivo do
    container. Um filho travado com outro vivo mantém o arquivo fresco.

!!! info "Duas linhas `INFO` do TaskIQ por batida do scheduler"
    O scheduler do TaskIQ loga `Sending task tempest:heartbeat ...` e
    `Scheduled task tempest:heartbeat has been cancelled.` a cada período.
    É o loop dizendo que passou por ali; nenhuma mensagem sai.

!!! check "Falha de escrita nunca derruba nada"
    Os arquivos são gravados de forma atômica (arquivo temporário +
    `os.replace`), com modo `0644` para um checker com outro usuário num
    volume compartilhado conseguir ler, então o healthcheck nunca lê um
    arquivo pela metade. Se a escrita falha (FS read-only, diretório
    ausente), sai **um** `WARNING` e o worker e o scheduler seguem; o
    arquivo velho é o sinal.

### O checker

```bash
python -m tempest_fastapi_sdk.tasks.heartbeat /tmp/worker.heartbeat --max-age 90
```

Sai `0` quando o arquivo existe e o horário gravado nele tem menos de
`--max-age` segundos, e `1` caso contrário — arquivo velho, ausente,
ilegível, mais de 5 s **no futuro** (`HEARTBEAT_FUTURE_TOLERANCE_SECONDS`:
relógio quebrado ou arquivo forjado não pode parecer saudável por horas) ou
argumento errado, `--max-age` que não é número finito acima de zero
incluído; nunca `2`, que o Docker reserva. Imprime uma linha com o motivo:

```text
healthy: heartbeat /tmp/worker.heartbeat is 0.2s old (max 90s)
unhealthy: heartbeat /tmp/worker.heartbeat is 10.4s old (max 6s)
```

Ele só usa a biblioteca padrão: roda com o pacote base instalado, sem o
extra `[tasks]` nem o `[cli]` (medido numa imagem `python:3.13-slim` com só o
wheel base). Em código, o mesmo teste é `is_heartbeat_fresh(path, max_age)` e
`heartbeat_age(path)`.

!!! tip "`--max-age` de umas três vezes o período"
    Com `TASKIQ_HEARTBEAT_SECONDS=30`, use `--max-age 90`: um heartbeat
    atrasado (worker ocupado, GC, relógio) não vira alarme falso.

### No `docker-compose.yml`

```yaml
x-tasks-env: &tasks-env
  TASKIQ_BROKER_URL: redis://redis:6379/0
  TASKIQ_WORKER_HEARTBEAT_FILE: /tmp/worker.heartbeat
  TASKIQ_SCHEDULER_HEARTBEAT_FILE: /tmp/scheduler.heartbeat
  TASKIQ_HEARTBEAT_SECONDS: "30"

services:
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10

  scheduler:
    build: .
    command: ["taskiq", "scheduler", "src.tasks:scheduler"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/scheduler.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  worker:
    build: .
    command: ["taskiq", "worker", "src.tasks:broker"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/worker.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  api:
    build: .
    command: ["python", "main.py"]
    depends_on:
      worker:
        condition: service_healthy
```

`start_period` cobre a subida (importar o serviço antes da primeira
batida). Cada checagem leva cerca de um segundo (importa o SDK), por isso o
`timeout: 10s`.

!!! danger "Docker puro **não** reinicia container `unhealthy`"
    O healthcheck **marca** o container (`docker ps` mostra `(unhealthy)`); a
    política `restart:` só age quando o processo **sai**. Medido com Docker
    29.6.1 e Compose 5.3.0: com os processos do worker congelados
    (`SIGSTOP`), a réplica virou `unhealthy` em ~20 s e ficou assim, com
    `RestartCount` em `0`, apesar do `restart: unless-stopped` — a outra
    réplica seguiu `healthy`. O que o healthcheck alimenta é:

    - `depends_on: condition: service_healthy` — a `api` acima só sobe depois
      que o worker provou que está vivo;
    - `docker ps` / `docker inspect` — o estado fica visível para quem opera;
    - o seu monitoramento (alerta sobre containers `unhealthy`), ou um
      orquestrador que reage a ele — Swarm e Kubernetes (via `livenessProbe`)
      substituem o container; o Docker sozinho, não.

## Scheduler no lifespan do FastAPI — e só uma réplica agendando

Um deploy pequeno não quer um segundo processo só para o cron. `tq.lifespan(scheduler=True)` sobe o scheduler dentro do web process, e plugga direto no FastAPI:

```python
# src/api/app.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.settings import settings
from src.tasks import tq


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Abre a fila e roda as tarefas periódicas neste processo."""
    async with tq.lifespan(scheduler=True):
        yield


app: FastAPI = FastAPI(title=settings.TITLE, lifespan=lifespan)
```

!!! danger "`start_scheduler()` cru dispara uma vez por réplica"
    O loop do scheduler não sabe quantos processos o rodam. Com três
    réplicas de uvicorn, `@tq.cron(every_n_minutes(1))` dispara **três
    vezes por minuto** — e nada levanta, nada é logado. O efeito chega
    como trabalho duplicado: a varredura que expira cobrança expira três
    vezes, o backup roda três vezes, o e-mail sai três vezes. Esse `três`
    é medido, não deduzido — é a segunda linha da saída abaixo.

    `scheduler=True` não tem esse problema porque não roda o loop
    direto: uma réplica segura um **lease** e roda; as outras ficam de
    prontidão e assumem quando o lease lapsa. Três lifespans abertos
    contra o mesmo lease, com os loops contados nos dois modos:

    ```text
    scheduler=True         loops correndo: 1   replica-1=1  replica-2=0  replica-3=0
    scheduler='unlocked'   loops correndo: 3   replica-1=1  replica-2=1  replica-3=1
    ```

    O `3` da segunda linha é o defeito, e o `1` da primeira é o lease
    resolvendo. `tests/tasks/test_scheduler_lease.py` fixa as duas
    contagens — a primeira parametrizada em 2, 3 e 5 réplicas — e um
    caso separado espera o líder sair e confirma que a de prontidão
    assume na primeira tentativa de `acquire` que começa depois que o
    líder solta o lease, sem esperar o TTL lapsar.

### De onde vem o lease

O lease é um lock em Redis, e o `TaskQueue` deriva a URL de onde ela já está:

* `TaskQueue.from_settings(settings)` ou `TaskQueue.redis(url)` → o próprio broker.
* `TaskQueue.from_settings(settings)` com broker AMQP e `TASKIQ_RESULT_BACKEND_URL` apontando pra Redis → o result backend.
* Broker construído na mão → nenhuma. Passe `scheduler_lock=`.

Sem lease derivável, `scheduler=True` **recusa no startup** em vez de agendar N vezes:

```text
ValueError: lifespan(scheduler=True) needs a lease so only one replica runs
the schedule, and none could be derived from this broker. Pass
scheduler_lock=RedisSchedulerLock.from_url(...), build the queue with
TaskQueue.from_settings(...) or TaskQueue.redis(...), or ask for
scheduler="unlocked" if you accept every replica firing every schedule.
```

Para coordenação própria — etcd, advisory lock no Postgres — implemente o `SchedulerLock`, que são três chamadas e não conhece Redis:

```python
from tempest_fastapi_sdk.tasks import RedisSchedulerLock, SchedulerLock

lease: SchedulerLock = RedisSchedulerLock.from_url(
    "redis://localhost:6379/0",
    name="alofans:scheduler",
    ttl_seconds=30.0,
)
```

!!! warning "Dois serviços numa Redis precisam de `name` diferente"
    O default é `"tempest:tasks:scheduler"`. Dois serviços compartilhando
    a instância disputam a mesma chave, e um deles **nunca** agenda.

!!! warning "Relógio que salta encurta o lease"
    O Redis grava a expiração de uma chave como timestamp Unix absoluto e
    consulta o relógio do computador mesmo com a instância rodando — a
    [documentação do `EXPIRE`](https://redis.io/docs/latest/commands/expire/#expires-and-persistence)
    dá o exemplo: chave com TTL de 1000 s e relógio adiantado 2000 s expira
    na hora. Para o lease do scheduler, isso quer dizer que **um salto para
    frente no relógio do servidor Redis maior que o TTL que resta derruba o
    lease** antes do prazo, e outra réplica pode adquiri-lo.

    Não é hipótese de laboratório: numa máquina WSL2 medimos o relógio de
    parede saltando +3,6 s duas vezes por minuto, e com um lease de 2 s o
    `tests/tasks/test_scheduler_lease.py` viu dois loops rodando ao mesmo
    tempo (o teste agora fixa o relógio do fakeredis para medir a eleição,
    não o host). Ajuste de NTP em *step* e VM retomada depois de pausa são
    as outras fontes comuns.

    O default de `ttl_seconds=30.0`, renovado a cada terço, mantém sempre
    pelo menos ~20 s de TTL restante — é o tamanho do salto que ele
    absorve. Em produção:

    * sincronize o relógio do host do Redis por *slew* (chrony/NTP), não
      por *step*;
    * não baixe o TTL para poucos segundos para "assumir mais rápido" — o
      líder que sai solta o lease no shutdown, então a troca planejada já
      não espera o TTL;
    * mantenha as tarefas agendadas idempotentes: o lease reduz disparo
      duplicado, não o torna impossível.

!!! info "`scheduler="unlocked"` existe, e tem que ser digitado"
    Quem roda réplica única de propósito passa `scheduler="unlocked"` e
    dispensa o lease. É uma escolha, e por isso não é o default: o default
    é `scheduler=False` (só o broker, sem agendar nada), e `True` é a forma
    guardada de ligar o scheduler. Quem esqueceu de pedir não agenda — não
    agenda N vezes.
