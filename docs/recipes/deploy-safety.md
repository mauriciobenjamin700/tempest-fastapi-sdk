# Deploy seguro (migrações + graceful shutdown)

Dois riscos clássicos de deploy: uma migration que **apaga dados** sem
querer, e um rollout que **corta requests no meio** quando o pod velho
morre. Esta receita cobre as duas defesas que o SDK traz.

## Migrações seguras: `safe_upgrade`

`AlembicHelper.safe_upgrade()` roda o upgrade **só se** nenhuma migration
pendente for destrutiva. Ele varre o `def upgrade()` de cada revision
pendente atrás de chamadas que apagam dados — `op.drop_table`,
`op.drop_column`, `op.drop_constraint` (e variantes `batch_op`) — e, se
achar alguma, levanta `DestructiveMigrationError` **sem tocar no banco**.

```python
from tempest_fastapi_sdk import AlembicHelper, DestructiveMigrationError


def deploy_migrations() -> None:
    """Aplica migrations no deploy, barrando DROPs acidentais."""
    helper: AlembicHelper = AlembicHelper(db_url="postgresql+asyncpg://...")
    try:
        helper.safe_upgrade("head")
    except DestructiveMigrationError as exc:
        # CI/CD falha aqui — alguém precisa revisar e liberar com force.
        for revision, op in exc.offences:
            print(f"bloqueado: {revision} → {op}")
        raise
```

A varredura olha o **código** da migration, não o SQL gerado — então não
dá falso-positivo no rebuild de tabela que o SQLite faz em batch mode. Um
`drop_*` no `downgrade()` (o caminho normal e esperado) é ignorado.

### Liberando um DROP intencional

Quando o DROP é proposital (você já fez backup, já validou), passe
`force=True` — as operações destrutivas são logadas e o upgrade roda:

```python
from tempest_fastapi_sdk import AlembicHelper

helper: AlembicHelper = AlembicHelper(db_url="postgresql+asyncpg://...")
helper.safe_upgrade("head", force=True)  # eu sei o que estou fazendo
```

!!! tip "Só inspecionar"
    `helper.pending_destructive_ops("head")` devolve a lista de
    `(revision, operação)` sem rodar nada — útil pra um passo de CI que só
    reporta.

!!! danger "force=True apaga dados"
    `DROP COLUMN` / `DROP TABLE` são irreversíveis. Só use `force=True`
    depois de backup e revisão humana.

## Graceful shutdown: drenar requests em voo

No rollout, o orquestrador manda `SIGTERM` e, depois de um tempo,
`SIGKILL`. Se uma request ainda estiver rodando quando o worker morre, ela
é cortada — vira um 502 intermitente. `GracefulShutdownMiddleware`:

1. Ao entrar em **drenagem**, responde `503` + `Retry-After` pra requests
   novas, então o load balancer para de rotear pra esse pod.
2. **Conta** as requests em voo; `wait_drained()` espera elas terminarem
   (com timeout) antes do processo sair.

Você segura a instância e dirige a drenagem pelo `lifespan` (o uvicorn
roda o shutdown do lifespan no `SIGTERM` — e é ele quem cuida do sinal):

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from tempest_fastapi_sdk import GracefulShutdownMiddleware

shutdown: GracefulShutdownMiddleware = GracefulShutdownMiddleware(drain_timeout=25.0)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Drena as requests em voo no shutdown."""
    yield
    shutdown.begin_drain()
    await shutdown.wait_drained()


app: FastAPI = FastAPI(lifespan=lifespan)
app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)
```

Configure o grace period do orquestrador um pouco **acima** do
`drain_timeout`, e o `--timeout-graceful-shutdown` do uvicorn pra casar.

!!! warning "O sinal é do seu servidor"
    O uvicorn já instala handlers de `SIGTERM` e dispara o shutdown do
    lifespan — dirija a drenagem por lá. O método opt-in
    `install_signal_handlers()` só serve pra servidores que **não**
    gerenciam sinais sozinhos; ele encadeia o handler anterior e é no-op
    fora da thread principal.

## Recap

- `AlembicHelper.safe_upgrade()` recusa migrations destrutivas
  (`DestructiveMigrationError`); `force=True` libera; `pending_destructive_ops()`
  só inspeciona.
- `GracefulShutdownMiddleware` responde `503` durante a drenagem e
  `wait_drained()` espera as requests em voo — dirigido pelo `lifespan`.
