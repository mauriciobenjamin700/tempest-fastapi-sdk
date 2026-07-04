# CLI


Instalar o `tempest-fastapi-sdk` expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

!!! tip "Erro de uso? O help completo aparece junto"
    Quando você digita um comando inexistente, uma opção inválida ou
    esquece um argumento obrigatório, o `tempest` imprime o `--help`
    **completo** daquele comando (todos os parâmetros, defaults e
    descrições) **antes** da linha de erro — em vez do `Try '... --help'`
    enxuto do Click. Você corrige na hora, sem reexecutar com `--help`.

    ```bash
    tempest user create            # esqueceu --email
    # ... help completo do `user create` (todas as opções) ...
    # Error: Missing option '--email' / '-e'.
    ```

### Gerar um novo serviço

```bash
tempest new my_service                          # gera em ./my_service
tempest new my_service --path ~/projects        # diretório-pai customizado
tempest new my_service \
    --bind-host 0.0.0.0 \                       # HOST padrão no .env.example
    --bind-port 9090 \                          # PORT padrão no .env.example
    --extras auth,upload                        # extras do SDK fixados
tempest new my_service --force                  # sobrescreve diretório existente
```

O esqueleto casa com a arquitetura em camadas documentada em [Arquitetura »](../architecture.md):

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # fixa tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # TITLE/VERSION/SERVER_HOST/SERVER_PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
├── docker-compose.yaml      # serviços baseados nos extras escolhidos
├── .gitignore
├── README.md
├── src/
│   ├── server.py            # uvicorn.run() + app FastAPI no nível do módulo
│   ├── api/
│   │   ├── app.py           # create_app() conecta middleware + handlers do SDK
│   │   ├── routers/         # router de negócio placeholder
│   │   └── dependencies/    # auth.py (require_token) + factories
│   ├── controllers/         # orquestração entre services
│   ├── services/            # lógica de negócio
│   ├── schemas/             # DTOs Pydantic
│   ├── core/                # settings.py + exceptions.py
│   ├── db/
│   │   ├── models/
│   │   └── repositories/
│   └── utils/
└── tests/
    └── test_smoke.py        # garante que /api/ e /health/liveness sobem
```

O `pyproject.toml` gerado fixa a versão atual do SDK (`tempest-fastapi-sdk[auth,admin]>=<versão>` por padrão — mude com `--extras`). O `.env.example` criado usa a nomenclatura de settings da v0.8.0 (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), e `src/server.py` delega a `tempest_fastapi_sdk.run_server` para que o uvicorn seja importado de forma preguiçosa e os testes possam importar o app sem ele. Regras de validação: o nome do projeto deve casar com `^[a-z][a-z0-9_]*$` e não pode colidir com uma palavra-chave do Python, então `tempest new Bad-Name` e `tempest new class` saem com código 2 antes de qualquer arquivo ser escrito.

!!! tip "Título / versão da API vêm do `.env`"
    A partir da v0.48.0 o `Settings` scaffoldado carrega `TITLE`,
    `VERSION` e `DESCRIPTION`, e `src/api/app.py` os consome
    (`FastAPI(title=settings.TITLE, version=settings.VERSION,
    description=settings.DESCRIPTION)`, `make_health_router(version=
    settings.VERSION)` e `AdminSite(title=f"{settings.TITLE} admin")`).
    Ajuste o título mostrado no Swagger/ReDoc e no header do `/admin`
    direto no `.env`, sem editar código:

    ```bash
    TITLE=Minha API
    VERSION=1.2.0
    DESCRIPTION=API de pagamentos do produto X.
    ```

### `docker-compose.yaml` baseado nos extras

Desde a v0.25.0 o scaffold gera um `docker-compose.yaml` com **apenas** os serviços que os extras escolhidos precisam — sem ZooKeeper, Kafka ou qualquer outra coisa que você não vai usar.

| Extra | Container subido | Porta(s) exposta(s) |
|-------|------------------|---------------------|
| (sempre) | `postgres:18-alpine` | 5432 |
| `[cache]` | `redis:8-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:4-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Exemplo — projeto que usa cache + uploads em S3 + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Gera:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` que cria o bucket `uploads`), `mailhog`
- `.env.example` com `REDIS_URL`, `MINIO_*`, `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` (MailHog é plain — sem STARTTLS)

!!! info "Credenciais vêm do `.env`, não estão hardcoded no compose"
    A partir da v0.37.0, nenhuma credencial é gravada direto no
    `docker-compose.yaml`. Cada bloco `environment:` usa a forma
    `${VAR:-default}`, e o Docker Compose resolve `VAR` a partir do
    `.env` ao lado do compose. O `:-default` mantém o stack subindo
    antes de você copiar `.env.example` para `.env` — mas defina
    segredos reais no `.env` para qualquer deploy não-descartável.
    Variáveis lidas pelo compose: `POSTGRES_USER` / `POSTGRES_PASSWORD`
    / `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`
    / `RABBITMQ_DEFAULT_VHOST`, `MINIO_ROOT_USER` /
    `MINIO_ROOT_PASSWORD` — todas com seus padrões já no `.env.example`.

Subir tudo:

```bash
docker compose up -d
```

Derrubar mantendo volumes:

```bash
docker compose down
```

Derrubar limpando volumes:

```bash
docker compose down -v
```

As tags das imagens são fixadas — atualize por `pyproject.toml` da SDK, não no projeto gerado. Versões atuais (v0.26.0+): `postgres:18-alpine`, `redis:8-alpine`, `rabbitmq:4-management-alpine`.

### Regerar o `docker-compose.yaml` num projeto existente

Quando você muda os extras instalados (`uv add "tempest-fastapi-sdk[minio]"`) ou o SDK bumpa as imagens, regenere com:

```bash
tempest generate --docker                        # lê extras do pyproject.toml local
tempest generate --docker --extras cache,minio   # força extras explicitos
tempest generate --docker --name my-svc          # sobrescreve prefixo do container
tempest generate --docker --force                # sobrescreve compose existente
```

O comando lê o `[project] name` + extras do `pyproject.toml` do diretório atual (use `--path` pra outro). Recusa overwrite sem `--force` pra não pisar em edits manuais. O `.env.example` é atualizado de forma idempotente — re-rodar não duplica blocos.

### Dockerfile para containerizar a app

Desde a v0.71.0, `tempest new` também gera um **`Dockerfile`** + **`.dockerignore`** prontos pra empacotar o serviço como imagem. O `Dockerfile` é **multi-stage** e usa [uv](https://docs.astral.sh/uv/):

- **stage `builder`** — instala as dependências num `/app/.venv` (camada cacheada que só re-roda quando `pyproject.toml` / `uv.lock` mudam), depois instala o projeto.
- **stage final** — copia só o venv + o código, roda como usuário **não-root** (`app`, uid 1000) e expõe a porta configurada.

```dockerfile
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY . .
RUN uv sync --no-dev

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" SERVER_HOST=0.0.0.0 SERVER_PORT=8000
USER app
EXPOSE 8000
CMD ["python", "main.py"]
```

Construir e rodar:

```bash
docker build -t my_service .
docker run --rm -p 8000:8000 --env-file .env my_service
```

!!! info "A imagem faz bind em `0.0.0.0` por padrão"
    O stage final define `ENV SERVER_HOST=0.0.0.0` para que a app
    seja acessível de fora do container mesmo sem `.env`. Localmente
    o scaffold mantém `SERVER_HOST=127.0.0.1` (serviço interno) — o
    container sobrescreve pra `0.0.0.0` porque ali o bind precisa
    aceitar conexões externas. Passe `--env-file .env` pra apontar
    `DATABASE_URL` na infra do `docker-compose.yaml`.

!!! warning "`docker-compose.yaml` continua só com infra"
    O compose gerado sobe **apenas** Postgres + os serviços dos extras
    (Redis, RabbitMQ, MinIO, MailHog) — não embute um serviço `app`.
    O `Dockerfile` é standalone: use `docker build` / `docker run`, ou
    adicione um serviço `app:` com `build: .` ao compose à mão se quiser
    subir tudo num comando só.

#### Regerar o Dockerfile — `tempest generate --dockerfile`

```bash
tempest generate --dockerfile                    # Dockerfile + .dockerignore
tempest generate --dockerfile --name my-svc      # sobrescreve nome nos comentários
tempest generate --dockerfile --force            # sobrescreve arquivos existentes
tempest generate --docker --dockerfile --src     # tudo numa tacada
```

A porta do `EXPOSE` / `SERVER_PORT` é lida do `SERVER_PORT` no `.env` (ou `.env.example`), caindo em `8000` se não achar. Como os outros generators, recusa overwrite sem `--force`.

### Gerar as camadas `src` dos extras — `tempest generate --src`

As camadas sempre presentes (`api`, `controllers`, `services`, `schemas`, `db`, `core`, `utils`) já vêm no scaffold. As camadas que só fazem sentido com um extra específico — `[queue]` (FastStream) e `[tasks]` (TaskIQ) — **não** entram no esqueleto base: deixar pacotes placeholder vazios em todo serviço contraria as regras de layout. Quando você adiciona um desses extras a um projeto existente (`uv add "tempest-fastapi-sdk[queue]"`), gere a camada correspondente com:

```bash
tempest generate --src                           # lê extras do pyproject.toml local
tempest generate --src --extras tasks            # força extras explícitos
tempest generate --src --force                   # sobrescreve arquivos existentes
tempest generate --docker --src                  # compose + camadas numa tacada
```

Mapeamento extra → camada gerada:

| Extra | Arquivos criados (sob `src/` ou `app/`) |
|-------|------------------------------------------|
| `[queue]` | `queue/__init__.py` (broker + `AsyncBrokerManager` + `get_broker`), `queue/handlers.py` (subscriber de exemplo) |
| `[tasks]` | `tasks/__init__.py` (broker + `AsyncTaskBrokerManager` + `get_task_manager`), `tasks/jobs.py` (task de exemplo) |

A raiz (`src` ou `app`) é detectada automaticamente, e os imports gerados (`from src.queue import broker`) já apontam pra ela. A operação é **idempotente**: arquivos existentes são **mantidos** a menos que você passe `--force`, então um handler editado à mão nunca é sobrescrito silenciosamente — o arquivo irmão que ainda não existe é criado normalmente. Extras sem camada associada (ex.: só `[cache]`) não geram nada e o comando avisa.

!!! note "`tempest new` já gera as camadas dos extras escolhidos"
    Um `tempest new my_service --extras auth,queue` já entrega
    `src/queue/` pronto — o `generate --src` é pra quando você adiciona
    o extra **depois** de criar o projeto.

Depois de gerar:

```bash
cd my_service
uv sync                                         # instala SDK + ferramentas de dev
cp .env.example .env
uv run python main.py                           # serve no HOST:PORT configurado
uv run pytest                                   # o smoke test embutido
```

### Banco de dados — `tempest db`

Wrapper Alembic. Usa o `AlembicHelper` por trás, então a configuração (`alembic.ini` + `env.py`) continua sendo a fonte da verdade.

Resolução do `DATABASE_URL` na seguinte ordem:

1. Flag `--database-url`.
2. Env var `DATABASE_URL`.
3. `src.core.settings.settings.DATABASE_URL` (quando rodando no diretório do projeto scaffoldado).
4. `sqlalchemy.url` do `alembic.ini`.

```bash
tempest db init                                  # cria alembic.ini + alembic/env.py
tempest db revision -m "init users table"        # autogenerate por padrão
tempest db revision -m "manual change" --manual  # cria arquivo vazio pra editar
tempest db upgrade                               # alembic upgrade head
tempest db upgrade <rev>                         # upgrade até rev específico
tempest db downgrade                             # rollback de 1 step
tempest db downgrade <rev>                       # rollback até rev específico
tempest db current                               # imprime revision aplicado
tempest db history                               # histórico de revisions
tempest db history -v                            # com message body completo
tempest db stamp head                            # marca o DB sem rodar migrations
tempest db squash -m "init" --yes                # colapsa o histórico em 1 migration
tempest db backup                                # dump em backups/<db>_<ts>.<ext>
tempest db backup -o dump.sql                    # plain SQL (Postgres) por extensão
tempest db restore dump.dump --yes               # restaura (clean + recreate)
tempest db seed                                  # roda src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # callable customizado
```

#### Colapsar o histórico — `tempest db squash`

Com o tempo o diretório `alembic/versions/` cresce sem limite — cada ajuste de schema vira mais um arquivo que o Alembic precisa percorrer em todo `upgrade`. O `squash` zera esse histórico para **uma única migration raiz** que descreve o schema **atual**, sem perder os bancos já existentes.

!!! danger "Operação destrutiva — rode contra um banco de desenvolvimento"
    O `squash` faz `downgrade base` no banco configurado (DROPa todas as tabelas) para conseguir autogerar o schema completo num arquivo só. Por isso exige `--yes`. Confirme que o `DATABASE_URL` aponta para um banco de dev antes de rodar.

O fluxo é:

1. Captura o `head` atual (vira o nome do diretório de backup).
2. `downgrade base` — limpa o banco para o autogenerate enxergar o schema vazio.
3. Move as revisions antigas para `alembic/versions/_squashed_<oldhead>/` (subdiretório que o Alembic ignora). Use `--no-backup` para apagar de vez.
4. Autogera **uma** migration raiz a partir de `BaseModel.metadata`.
5. `upgrade head` recria o schema e marca a nova revision.

```bash
tempest db squash -m "init" --yes               # backup recuperável (padrão)
tempest db squash -m "init" --yes --no-backup   # apaga os arquivos antigos
```

!!! warning "Bancos de produção não são tocados"
    O `squash` só mexe no banco configurado. Depois de fazer deploy da árvore colapsada, marque os bancos de produção como migrados **sem** recriar tabelas:

    ```bash
    tempest db stamp head
    ```

!!! tip "Squash manual (sem perder dados) → `stamp --purge`"
    Se você unificar as migrations **à mão** (apaga `versions/`, escreve 1 migration baseline, mantém o banco com os dados), o `alembic_version` ainda aponta para a revision antiga — que não existe mais no tree. Um `stamp` normal falha com `Can't locate revision`. Use `--purge` para limpar o ponteiro órfão e gravar o novo baseline:

    ```bash
    tempest db stamp init_schema --purge
    ```

**Recap:** `squash` troca um histórico que cresce sem fim por uma migration inicial limpa; `stamp` reconcilia os bancos que já estão no schema final (use `--purge` quando a revision gravada não existir mais). Backup recuperável por padrão — o Git é a sua segunda rede.

#### Backup e restore — `tempest db backup` / `tempest db restore`

Snapshot do banco para um arquivo e volta. A estratégia muda por dialeto, mas a CLI é a mesma:

- **PostgreSQL** — `pg_dump` / `pg_restore`. O formato vem da extensão do arquivo: `.dump` → custom (`pg_dump -Fc`, comprimido, restaura com `pg_restore`), `.sql` → plain (`psql`). Force com `--plain` / `--custom`. Exige os client tools do Postgres no `PATH`.
- **SQLite** — cópia do arquivo do banco.

!!! warning "Pré-requisito: client tools do Postgres"
    `backup` / `restore` num banco **PostgreSQL** dependem dos binários `pg_dump`, `pg_restore` e `psql` disponíveis no `PATH`. Eles **não** vêm com o pacote Python — são instalados pelo sistema operacional. Sem eles, a CLI falha com uma mensagem clara (`'pg_dump' not found on PATH`).

    **SQLite não precisa de nada** — o backup é uma cópia de arquivo feita pela stdlib.

    === "Debian / Ubuntu"
        ```bash
        sudo apt-get update && sudo apt-get install -y postgresql-client
        ```

    === "Fedora / RHEL"
        ```bash
        sudo dnf install -y postgresql
        ```

    === "Arch"
        ```bash
        sudo pacman -S postgresql
        ```

    === "macOS (Homebrew)"
        ```bash
        brew install libpq && brew link --force libpq
        ```

    === "Windows"
        ```powershell
        choco install postgresql   # Chocolatey
        scoop install postgresql   # ou Scoop
        ```

    !!! tip "Confira a instalação"
        ```bash
        pg_dump --version && pg_restore --version && psql --version
        ```
        Combine a versão do client com a do servidor (`pg_dump` de uma major mais nova lê servidores mais antigos, mas o contrário falha).

```bash
tempest db backup                       # backups/<db>_<YYYYMMDD-HHMMSS>.dump
tempest db backup -o snapshot.sql       # plain SQL (Postgres) pela extensão
tempest db backup -o snap.dump --custom # força formato custom
tempest db restore snapshot.sql --yes   # restaura (psql)
tempest db restore snap.dump --yes      # restaura (pg_restore --clean --if-exists)
```

!!! danger "Restore sobrescreve o banco de destino"
    Por padrão o restore é **clean + recreate**: dropa os objetos existentes antes de recriar, então o resultado é uma cópia fiel do backup (`pg_restore --clean --if-exists`; no plain dropa/recria o schema `public`; no SQLite sobrescreve o arquivo). Por isso exige `--yes`. Use `--no-clean` para aplicar o dump por cima do schema atual.

!!! info "A senha do Postgres não vaza no `ps`"
    A URL é parseada em `-h/-p/-U/-d` e a senha vai via `PGPASSWORD` no environment do subprocesso — nunca na linha de comando.

**Recap:** `backup` tira um snapshot (formato pela extensão no Postgres, cópia no SQLite); `restore --yes` traz de volta, limpando o destino por padrão.

#### Popular o banco — `tempest db seed`

Roda um callable de seed do projeto dentro de uma sessão gerenciada (commit no sucesso, rollback no erro). O callable recebe uma `AsyncSession` posicional e pode ser sync ou async; o que ele insere é decisão sua — o SDK só cuida do ciclo de vida da sessão. Por padrão importa `src.db.seeds:seed`.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CategoryModel


async def seed(session: AsyncSession) -> int:
    """Popula categorias iniciais. Retorna a contagem (opcional)."""
    session.add_all([CategoryModel(name="Livros"), CategoryModel(name="Games")])
    await session.flush()
    return 2
```

Quando o callable devolve um `int`, a CLI mostra a contagem: `Seeded via src.db.seeds:seed (2 rows).`

### Usuários — `tempest user`

Insere/lista usuários direto no banco usando o `UserModel` concreto do projeto (default `src.db.models:UserModel`). Útil pra bootstrapear o primeiro admin sem rodar SQL manual.

```bash
# Cria usuário comum
tempest user create --email ana@example.com --password senha-forte-12 --no-admin

# Cria admin (pode logar no /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Pede senha interativamente (não fica no shell history)
tempest user create --email admin@local --admin

# Modelo customizado fora do layout scaffoldado — DEVE ser subclasse de BaseUserModel
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Promove/rebaixa um usuário existente (liga/desliga is_admin)
tempest user promote --email ana@example.com    # vira admin
tempest user revoke  --email ana@example.com    # volta a ser comum

# Lista
tempest user list                                # todos
tempest user list --admin                        # só admins
```

!!! tip "Sem `--admin`/`--no-admin`, o create pergunta"
    Quando você **não** passa nem `--admin` nem `--no-admin` num
    terminal interativo, o `tempest user create` pergunta
    `Should this user be an administrator? [y/N]`. Em execuções
    não-interativas (CI, pipes, scripts) o prompt é pulado e o usuário
    nasce comum (`is_admin=False`) — passe `--admin` explicitamente pra
    criar admin sem TTY.

`tempest user promote` / `tempest user revoke` localizam o usuário por email (case-insensitive) e só alternam `is_admin`. Quando nenhum usuário casa com o email, saem com código 1 e a mensagem `no user found`.

Resolução do `DATABASE_URL` igual ao `tempest db` (env var > settings > alembic.ini).

### Segredos — `tempest secrets`

Gera e rotaciona os segredos da aplicação (`JWT_SECRET` / `TOKEN_SECRET` por padrão), reescrevendo as linhas correspondentes no `.env` **no lugar** — fazendo backup do arquivo antigo antes — e deixando as outras linhas intactas.

```bash
# Rotaciona JWT_SECRET e TOKEN_SECRET no .env (gera .env.bak)
tempest secrets rotate

# Só imprime os novos valores (não escreve nada) — pra pipar num secret manager
tempest secrets rotate --print

# Chaves e arquivo customizados
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# Mais entropia, sem backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotacionar `JWT_SECRET` invalida todo token assinado com o valor antigo: usuários são deslogados e links de reset/ativação pendentes param de funcionar. Rotacione numa janela de manutenção e reinicie o serviço pra carregar os novos valores.

### Gates de qualidade

Os comandos de lint chamam a ferramenta do projeto. Eles procuram o executável no `PATH` primeiro e, caso contrário, caem para `uv run <tool>` para que um virtualenv local do projeto funcione sem ativação manual.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix . + ruff format .   (escreve)
tempest fix --unsafe                            # também aplica os --unsafe-fixes do ruff
tempest format                                  # ruff format .          (escreve)
tempest fmt-check                               # ruff format --check .   (somente leitura)
tempest type                                    # mypy .
tempest test                                    # pytest
tempest test tests/api/                         # pytest com filtro de caminho
tempest check                                   # lint + fmt-check + type + test, para no primeiro erro
```

`tempest fix` é a passada única de "organize o projeto" — ordena e remove imports duplicados, descarta imports não usados, normaliza aspas de strings, remove espaços em branco no fim das linhas e então roda `ruff format` para alinhar indentação, comprimento de linha, linhas em branco e a quebra de linha final. Rode-o antes do push quando o CI fica pegando detalhes de estilo.

!!! info "O `ruff format` roda sempre — mesmo com erros restantes"
    O `ruff check --fix` sai com código ≠ 0 quando sobra **qualquer**
    violação que ele não consegue corrigir sozinho (uma string ou
    comentário longo demais, um nome indefinido, …). O `tempest fix`
    roda o `ruff format` mesmo assim, então uma única linha não-corrigível
    não impede a formatação do arquivo inteiro — as linhas de **código**
    longas continuam sendo quebradas e as linhas em branco extras
    removidas. O exit code do lint ainda é propagado depois, então o CI
    continua falhando nas pendências reais.

!!! warning "Strings e comentários longos não quebram"
    Nem o `ruff format` nem o `tempest fix` quebram **strings literais ou
    comentários** longos — comportamento idêntico ao Black. Essas linhas
    `E501` permanecem e precisam ser encurtadas à mão ou silenciadas com
    `# noqa: E501`.

Todo comando retorna o exit code da ferramenta subjacente, então `tempest check` é seguro para conectar ao CI (`tempest check || exit 1`) ou a hooks de pre-commit. Quando nem o executável nem o `uv` estão no `PATH`, o wrapper imprime `error: '<tool>' is not on PATH and 'uv' is unavailable` e sai com `127` em vez de falhar silenciosamente.

---

## Próximos passos

**Recap:** o `tempest` cobre o ciclo inteiro — do scaffold (`new`),
passando pela infra (`generate --docker` / `--dockerfile` / `--src`),
migrações (`db`), usuários (`user`) e segredos (`secrets`), até os gates
de qualidade (`lint` / `fix` / `type` / `test` / `check`). Depois de gerar
o serviço, siga para as receitas relacionadas:

- [Banco de dados »](database.md) — `BaseRepository`, sessões async e o
  fluxo de migrações por trás do `tempest db`.
- [Filas e tarefas »](queue-tasks.md) — as camadas que `tempest generate
  --src` gera para os extras `[queue]` (FastStream) e `[tasks]` (TaskIQ).
- [Deploy seguro »](deploy-safety.md) — migrações destrutivas e shutdown
  gracioso ao containerizar com o `Dockerfile` gerado.
