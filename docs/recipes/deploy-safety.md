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

## Backup antes de migrar (`DatabaseBackup`)

`safe_upgrade` recusa a migration destrutiva, mas às vezes o DROP é proposital.
A ordem certa nesse caso é backup → `force=True` → validar. `DatabaseBackup`
faz o dump a partir da mesma `DATABASE_URL` do serviço:

```python
# scripts/deploy.py
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup: DatabaseBackup = DatabaseBackup(settings.DATABASE_URL)
written: Path = backup.backup()
print(f"dump em {written}")
```

O sufixo `+asyncpg`/`+aiosqlite` da URL é removido sozinho — você passa a URL da
aplicação, sem manter uma segunda variável só pro backup. Sem `output=`, o
arquivo sai em `backups/<db>_<AAAAMMDD-HHMMSS>.<ext>`.

| Backend | `backup()` usa | Formato |
| --- | --- | --- |
| `postgresql` | `pg_dump` | custom (`-Fc`) por default; `.sql` no `output` (ou `plain=True`) faz dump texto |
| `sqlite` | cópia de arquivo | o próprio `.sqlite` |

Restaurar é o espelho — o formato sai da extensão:

```python
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup = DatabaseBackup(settings.DATABASE_URL)


backup.restore(Path("backups/app_20260727-104500.dump"))
```

`clean=True` (default) derruba os objetos antes de recriar, então a restauração
é uma cópia fiel: `pg_restore --clean --if-exists` no formato custom, `DROP
SCHEMA public CASCADE` antes do `psql -f` no plain, sobrescrita do arquivo no
SQLite. Passe `clean=False` pra restaurar **por cima** de um banco existente.

### Banco em container: `docker_container=`

Quando o Postgres roda no container dele, instalar `postgresql-client` na imagem
da aplicação — e mantê-lo numa versão compatível com o servidor — é peso morto
só pro job noturno. A imagem do banco já tem o `pg_dump` exato da versão dela:

```python
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup = DatabaseBackup(settings.DATABASE_URL, docker_container="app-db")

written: Path = backup.backup(Path("backups/app.dump"))
backup.restore(written)
```

Com `docker_container` setado, o `pg_dump` roda **dentro** do container e o dump
volta pelo stdout para o arquivo local; o restore faz o caminho inverso, com o
arquivo entrando pelo stdin do `pg_restore`/`psql`. Sem ele, nada muda.

!!! note "Três detalhes que fazem esse modo funcionar"
    - **`-h`/`-p` são descartados.** O host e a porta da URL descrevem como a
      *aplicação* alcança o banco de fora; dentro do container esse caminho não
      existe. Usuário e database continuam vindo da URL.
    - **A senha atravessa por nome.** O comando carrega `-e PGPASSWORD`, sem
      valor: o Docker copia do ambiente do processo que chamou. Escrever
      `-e PGPASSWORD=…` colocaria a senha na linha de comando do container, que
      qualquer `ps` no host lê.
    - **Nada é copiado para dentro.** O restore transmite pelo stdin em vez de
      `docker cp`, então não sobra arquivo temporário no container nem janela em
      que ele está pela metade.

    O que o modo exige é o `docker` no `PATH` de quem chama — e é isso que o
    `BackupToolMissingError` passa a checar aqui, no lugar do `pg_dump`.

!!! warning "Os dois erros que você vai ver primeiro"
    - `BackupToolMissingError` — `pg_dump`/`pg_restore`/`psql` (ou o `docker`,
      no modo acima) não está no `PATH`. Container de app raramente traz o
      client do Postgres; instale `postgresql-client` na imagem que roda o
      deploy, use `docker_container=`, ou rode o backup de outro lugar.
    - `UnsupportedBackupBackendError` — dialeto sem estratégia (MySQL, SQL
      Server). Só Postgres e SQLite são cobertos.

    Os dois são levantados **antes** de criar `backups/`, então uma falha nunca
    deixa um diretório vazio pra alguém confundir com backup feito.

!!! info "Métodos síncronos, de propósito"
    `pg_dump` é processo, cópia de arquivo é I/O de disco — nada disso ganha com
    `async`. Chame de um comando de CLI ou script de deploy; se precisar de
    dentro de código async, use `asyncio.to_thread(backup.backup)`.

## Graceful shutdown: drenar requests em voo

No rollout, o orquestrador manda `SIGTERM` e, depois de um tempo,
`SIGKILL`. Se uma request ainda estiver rodando quando o worker morre, ela
é cortada — vira um 502 intermitente. `GracefulShutdownMiddleware`:

1. Ao entrar em **drenagem**, responde `503` + `Retry-After` pra requests
   novas — inclusive no endpoint de health, que é o que faz o load balancer
   parar de rotear pra esse pod.
2. **Conta** as requests em voo; `wait_drained()` espera elas terminarem
   (com timeout).

### O que o uvicorn já faz sozinho no `SIGTERM`

Antes de escolher onde ligar a drenagem, veja o que acontece sob o uvicorn.
Medido com o uvicorn num subprocesso, uma request de 3 s em voo e `SIGTERM`
0,5 s depois:

```text
after SIGTERM: /health ConnectError
in-flight /slow: 200
LIFESPAN-SHUTDOWN in_flight=0
LIFESPAN-DRAINED True
```

O uvicorn **fecha o listener na hora** (a request nova nem conecta), **espera
ele mesmo** a request em voo terminar e só **depois** roda o shutdown do
lifespan. Ou seja: um `begin_drain()` no shutdown do lifespan chega quando
não há mais request nenhuma — nunca emite um `503`, e o `wait_drained()`
volta `True` na hora. A espera pelas requests em voo, sob o uvicorn, é a do
próprio uvicorn, limitada pelo `--timeout-graceful-shutdown`.

### Drenar **antes** do `SIGTERM`

O `503` só serve se sair enquanto o listener ainda aceita conexão — ou seja,
antes do `SIGTERM`. No Kubernetes, esse momento é o hook `preStop`, que roda
antes do sinal. Ligue a drenagem a um sinal que o uvicorn **não** usa
(`SIGUSR1`) e mande esse sinal do `preStop`:

```python
import signal

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from tempest_fastapi_sdk import GracefulShutdownMiddleware

shutdown: GracefulShutdownMiddleware = GracefulShutdownMiddleware(drain_timeout=25.0)

app: FastAPI = FastAPI()
app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)
shutdown.install_signal_handlers((signal.SIGUSR1,))


@app.get("/health")
async def health() -> dict[str, str]:
    """Readiness: responde 503 assim que a drenagem começa."""
    return {"status": "ok"}
```

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "kill -USR1 1 && sleep 15"]
```

Medido com o mesmo app, mandando `SIGUSR1` antes do `SIGTERM`:

```text
after SIGUSR1: /health 503 Retry-After= 5
after SIGTERM: /health ConnectError
in-flight /slow: 200
```

Depois do `SIGUSR1`, toda request nova recebe `503` com `Retry-After` — a
readiness probe falha e o pod sai do balanceamento — enquanto a request em
voo termina com `200`. O `sleep` do `preStop` dá tempo pra isso acontecer; o
`SIGTERM` chega depois, e o uvicorn espera o que ainda estiver em voo.

!!! warning "Um processo por pod"
    `kill -USR1 1` alcança o processo de PID 1 do container — o seu
    `python main.py` quando o `CMD` está na forma exec. Com
    `uvicorn --workers N`, o PID 1 é o supervisor e o sinal não chega aos
    workers; nesse caso rode um worker por pod, ou mande o sinal para cada
    worker.

!!! info "`install_signal_handlers` precisa da thread principal"
    O `signal.signal` só funciona na thread principal; fora dela o método não
    faz nada. A medição acima chama o método no nível do módulo de um
    `main.py` que sobe com `uvicorn.run(app)`. Não passe `SIGTERM` nem
    `SIGINT`: esses são do uvicorn.

Configure o grace period do orquestrador (`terminationGracePeriodSeconds`)
acima da soma do `sleep` do `preStop` com o `--timeout-graceful-shutdown` do
uvicorn.

## Recap

- `AlembicHelper.safe_upgrade()` recusa migrations destrutivas
  (`DestructiveMigrationError`); `force=True` libera; `pending_destructive_ops()`
  só inspeciona.
- `DatabaseBackup(url).backup()` / `.restore(path)` — dump e restauração por
  dialeto (Postgres via `pg_dump`/`pg_restore`, SQLite por cópia), a partir da
  mesma `DATABASE_URL` do serviço.
- `GracefulShutdownMiddleware` responde `503` durante a drenagem e
  `wait_drained()` espera as requests em voo. Sob o uvicorn, dispare a
  drenagem **antes** do `SIGTERM` (`install_signal_handlers((signal.SIGUSR1,))`
  + `preStop`): no shutdown do lifespan o listener já fechou e não há
  request pra recusar.
