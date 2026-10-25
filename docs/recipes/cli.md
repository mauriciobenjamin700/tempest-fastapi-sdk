# CLI


Instalar o `tempest-fastapi-sdk[cli]` (ou o `[cli-ops]`, no servidor) expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

!!! info "A CLI é o extra `[cli]` (desde a 0.308.0)"
    O `tempest` mora num extra, e não no pacote base. Em desenvolvimento,
    instale do jeito que você usa (para o servidor, veja
    [`[cli-ops]`](#servidor-x-desenvolvimento-cli-ops-e-cli)):

    ```bash
    uv tool install "tempest-fastapi-sdk[cli]"   # comando global
    uv add --dev "tempest-fastapi-sdk[cli]"      # no grupo dev do projeto
    ```

    O motivo é o `ruff`: o gate (`tempest check`) vem do `tempest-cli`, que
    exige `ruff>=0.8.0` em runtime. No pacote base, todo serviço que importa
    o SDK levava um formatador para produção, e um projeto que fixa
    `ruff<0.8` no grupo dev nem conseguia resolver o lock.

    Sem nenhum dos dois extras, o script continua instalado e explica o que
    falta, com saída 2 e sem traceback:

    ```text
    $ tempest --help
    error: missing typer, click. The tempest CLI needs the optional [cli-ops] or [cli] extra. Install it with:
      uv add "tempest-fastapi-sdk[cli-ops]"   (on a server, without ruff)
      uv add --dev "tempest-fastapi-sdk[cli]"   (in development, with the gate)
      uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
    ```

    `import tempest_fastapi_sdk` não precisa de extra nenhum; ler
    `tempest_fastapi_sdk.cli.app` (ou importar `tempest_fastapi_sdk.cli.main`)
    sem o `typer` levanta `ImportError` com essa instrução. Importar
    `.lint` ou `.pr_prompt`, ou ler `TempestConfig` /
    `load_tempest_config` de `tempest_fastapi_sdk.cli.config`, sem o
    `tempest-cli` levanta `ImportError` apontando o `[cli]`.

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

### Servidor x desenvolvimento: `[cli-ops]` e `[cli]`

O `tempest` faz dois tipos de trabalho, e eles moram em ambientes diferentes:

- **Operar o serviço** — `tempest doctor`, `tempest db`,
  `tempest shell`, `tempest user`, `tempest routes` e os comandos do próprio
  projeto (`[tool.tempest] commands`). Isso você quer **no servidor**, com a
  configuração e a rede de lá.
- **O gate de qualidade** — `lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check` e `pr-prompt`. Isso é `ruff`, `mypy` e `pytest`, e mora
  **no desenvolvimento e na CI**.

Por isso existem dois extras:

| Extra | Traz | Onde usar |
| --- | --- | --- |
| `[cli-ops]` | `typer`, `click` | dependência de runtime, na imagem de produção |
| `[cli]` | `[cli-ops]` + `tempest-cli` (que exige `ruff`) | grupo `dev` e `uv tool install` |

O `[cli]` contém o `[cli-ops]`, então o projeto declara os dois sem conflito:

```toml
[project]
dependencies = [
    "tempest-fastapi-sdk[cli-ops,postgres]",
]

[dependency-groups]
dev = [
    "tempest-fastapi-sdk[cli]",
]
```

Com `uv sync --no-dev` (o que o `Dockerfile` gerado roda), a imagem fica com
o `tempest` e sem `tempest-cli` nem `ruff`. Num venv limpo com só o
`[cli-ops]`, `import ruff` e `import tempest_cli` falham, e o resto funciona:

```text
$ tempest doctor
OK    python         3.12.7 (...)
OK    sdk            tempest-fastapi-sdk <versão>
SKIP  settings       no <root>/core/settings.py here — run inside the project root
...
$ tempest staff-create ana@example.com      # de [tool.tempest] commands
staff created: ana@example.com
```

Os comandos do gate continuam listados no `tempest --help` — com a ajuda
`Quality gate command; needs the [cli] extra (tempest-cli and ruff).` — e,
chamados, explicam o que falta em vez de sumir num `No such command`. Saem
com código 2, sem traceback, com qualquer argumento que você tenha passado:

```text
$ tempest lint
error: tempest lint is not available. The quality gate (lint, fix, format, fmt-check, type, test, check, pr-prompt) needs the optional [cli] extra, which adds tempest-cli and ruff. Install it with:
  uv add --dev "tempest-fastapi-sdk[cli]"   (in a project)
  uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
```

!!! tip "Pré-compile o bytecode na imagem: `UV_COMPILE_BYTECODE=1`"
    O `uv` não compila os `.pyc` ao instalar, então o Python compila cada módulo
    na primeira vez que o importa. Se o container roda com um usuário sem
    permissão de escrita no `site-packages` — o caso comum numa imagem com
    usuário não-root —, os `.pyc` nunca são gravados e **toda** execução paga
    essa compilação, não só a primeira. Medido com
    `tempest-fastapi-sdk[cli-ops]==0.326.0`, `tempest --help`, 3 rodadas de
    cada:

    | Instalação | 1ª execução | Seguintes |
    | --- | --- | --- |
    | sem `UV_COMPILE_BYTECODE` | ~0,34 s | ~0,14 s |
    | sem, com `site-packages` só leitura | ~0,34 s | ~0,34 s |
    | com `UV_COMPILE_BYTECODE=1` | ~0,14 s | ~0,14 s |

    O `Dockerfile` que o `tempest new` gera já liga a variável. Num Dockerfile
    seu, ou num `uv pip install` / `uv tool install`, ligue-a no passo de
    instalação, para a compilação acontecer uma vez, no build:

    ```dockerfile
    ENV UV_COMPILE_BYTECODE=1
    RUN uv sync --locked --no-dev
    ```

    O mecanismo é o mesmo para todo módulo que a app importa ao subir, não só
    para o `tempest`.

!!! info "Detalhes técnicos"
    A leitura do `[tool.tempest]` que acha o projeto e os `commands`
    (`find_pyproject`, `load_tempest_table`, `load_project_commands` em
    `tempest_fastapi_sdk.cli.config`) é código do SDK, só com a stdlib,
    portada do `tempest-cli`. O `typing_strictness` e as flags de
    `ruff`/`mypy` continuam sendo do `tempest-cli` e só carregam quando um
    comando do gate roda.

### Gerar um novo serviço

```bash
tempest new my_service                          # gera em ./my_service
tempest new my_service --path ~/projects        # diretório-pai customizado
# HOST/PORT padrão no .env.example e extras do SDK fixados no pyproject.toml
tempest new my_service --bind-host 0.0.0.0 --bind-port 9090 --extras upload
tempest new my_service --force                  # sobrescreve diretório existente
```

O esqueleto casa com a arquitetura em camadas documentada em [Arquitetura »](../architecture.md):

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # fixa tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # TITLE/DESCRIPTION, SERVER_*, LOG_*, DATABASE_URL, JWT_* (com [auth]), TOKEN_SECRET, CORS_*
├── alembic.ini              # caminhos ancorados em %(here)s
├── alembic/                 # env.py + versions/ com a revision baseline
├── docker-compose.yaml      # serviços baseados nos extras escolhidos
├── Dockerfile               # multi-stage com uv, usuário não-root
├── .dockerignore
├── .gitignore
├── README.md
├── CLAUDE.md                # regras do projeto para agentes de IA e humanos
├── src/
│   ├── __init__.py          # re-exporta run de .server
│   ├── server.py            # app no nível do módulo + run() via run_server
│   ├── api/
│   │   ├── app.py           # create_app() conecta middleware, handlers e build_api_router()
│   │   ├── routers/         # api.py (build_api_router, o único /api) + service_info.py (GET /api/)
│   │   └── dependencies/    # auth.py + resources.py (db, get_session, get_mailer/get_storage por extra) + controllers.py (providers)
│   ├── controllers/system/  # ServiceInfoController — um pacote por contexto, uma classe por módulo
│   ├── services/            # lógica de negócio, em services/<contexto>/
│   ├── schemas/             # DTOs Pydantic
│   ├── scripts/             # composition.py — o composition root (build_*)
│   ├── core/                # settings.py + enums.py + types.py + exceptions.py
│   ├── db/
│   │   ├── configs/names.py # nomes de tabela num símbolo só
│   │   ├── models/users/    # UserModel — só com [auth] (ou [admin])
│   │   └── repositories/
│   ├── queue/               # só com o extra [queue] — broker + handlers
│   ├── tasks/               # só com [tasks] ou [tasks-redis] — queue, names, dispatch, worker + uma task de exemplo
│   ├── ui/                  # só com o extra [ssr] — pages/layout/components/styles
│   └── utils/
└── tests/
    ├── conftest.py          # um SQLite por worker do pytest-xdist
    ├── test_smoke.py        # garante que /api/ e /health/liveness sobem
    └── test_schemas.py      # todo campo de schema exportado documentado (assert_schema_metadata)
```

!!! tip "O `CLAUDE.md` gerado é o contrato do projeto"
    Todo projeto novo nasce com um `CLAUDE.md` que fixa as regras que
    mantêm os serviços parecidos entre si: a direção das dependências
    entre camadas, a **ordem exata** dos sete passos de uma feature nova
    (schema → model → repository → service → controller → provider →
    router), a tabela do que **não** reimplementar porque o SDK já
    entrega, e o "definition of done" que termina em `tempest check`.

    Ele existe para um agente de IA ler antes de escrever a primeira
    linha — e por isso os exemplos dele são verificados na CI do SDK:
    `tests/cli/test_scaffold_runtime.py` escreve o domínio de exemplo
    num projeto scaffoldado e o executa (POST 201, duplicado 409 com o
    `code` certo, listagem paginada no envelope do SDK). Um símbolo
    renomeado no SDK quebra o teste, não o projeto de quem usa.

O `pyproject.toml` gerado fixa a versão atual do SDK com os extras que você pediu, e só eles (`tempest-fastapi-sdk>=<versão>` sem `--extras`). `admin` e `auth` são **opt-in**: com `--extras auth` o projeto ganha o `UserModel`, o `JWTSettings` no `Settings` e o bloco `JWT_*` no `.env.example`; com `--extras admin` o `src/api/app.py` também monta o painel admin — e o `auth` entra junto, porque o login do painel verifica a senha pelo `PasswordUtils`. Sem nenhum dos dois, o serviço nasce sem tabela de usuário, sem painel e sem `bcrypt`/`PyJWT` no lock: `--extras postgres` fixa só `[postgres]`, `--extras admin,cache` fixa `[admin,auth,cache]`, e duplicatas somem com a lista saindo em ordem alfabética. Até a 0.312.x `admin` e `auth` eram fixados sempre. O grupo `dev` ganha `tempest-fastapi-sdk[cli]>=<versão>`, para o `tempest` rodar no projeto sem entrar na imagem: o `Dockerfile` gerado instala com `uv sync --no-dev`. O `.env.example` criado usa a nomenclatura de settings da v0.8.0 (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), e `src/server.py` delega a `tempest_fastapi_sdk.run_server` para que o uvicorn seja importado de forma preguiçosa e os testes possam importar o app sem ele. Regras de validação: o nome do projeto deve casar com `^[a-z][a-z0-9_]*$` e não pode colidir com uma palavra-chave do Python, então `tempest new Bad-Name` e `tempest new class` saem com código 2 antes de qualquer arquivo ser escrito.

!!! tip "Título, descrição e versão da API"
    A partir da v0.48.0 o `Settings` scaffoldado carrega `TITLE`,
    `VERSION` e `DESCRIPTION`, e `src/api/app.py` os consome
    (`FastAPI(title=settings.TITLE, version=settings.VERSION,
    description=settings.DESCRIPTION)`, `make_health_router(version=
    settings.VERSION)` e, com `[admin]`, `AdminSite(title=f"{settings.TITLE} admin")`).

    A versão tem **uma fonte só**: o `VERSION` do `Settings` sai de
    `[project].version` no `pyproject.toml`, lido por
    [`read_project_version`](../reference.md#tempest_fastapi_sdk.settings.version.read_project_version).
    O caminho é ancorado no próprio `settings.py` (não no cwd), então a
    versão é a do projeto mesmo rodando de outro diretório; bumpar a
    release é editar o `pyproject.toml`, e o `/health/readiness` e o
    `info.version` do OpenAPI acompanham. Título e descrição continuam no
    `.env`, e a env `VERSION` ainda sobrepõe o arquivo quando você precisa
    publicar uma versão diferente do que está commitado:

    ```bash
    TITLE=Minha API
    DESCRIPTION=API de pagamentos do produto X.
    # VERSION=1.2.0  # opcional: sobrepõe [project].version
    ```

### `docker-compose.yaml` baseado nos extras

Desde a v0.25.0 o scaffold gera um `docker-compose.yaml` com **apenas** os serviços que os extras escolhidos precisam — sem ZooKeeper, Kafka ou qualquer outra coisa que você não vai usar.

| Extra | Container subido | Porta(s) publicada(s) em `127.0.0.1` |
|-------|------------------|--------------------------------------|
| (sempre) | `postgres:18-alpine` | 5432 |
| `[cache]` / `[tasks-redis]` | `redis:8-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:4-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `quay.io/minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Toda porta da infra é publicada **só no loopback** (`"127.0.0.1:5432:5432"`):
o processo que você roda no host continua alcançando o Postgres em
`localhost:5432`, e nada de fora da máquina alcança. A API não precisa dessas
portas — dentro do compose ela fala com a infra pelo nome do serviço (veja o
[profile `prod`](#profile-prod-a-api-dentro-do-compose)).

Exemplo — projeto que usa cache + uploads em S3 + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Gera:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` que cria o bucket `uploads`), `mailhog`
- `.env.example` com `REDIS_URL`, `MINIO_ROOT_*` (o container), `STORAGE_*` (o app), `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` (MailHog é plain — sem STARTTLS)

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

Subir só a infra — o fluxo de dev, com a app rodando no host e reload:

```bash
docker compose up -d
uv run python main.py
```

Derrubar mantendo volumes:

```bash
docker compose --profile prod down
```

Derrubar limpando volumes:

```bash
docker compose --profile prod down -v
```

!!! warning "`down` sem `--profile prod` deixa a API de pé"
    Um `docker compose down` puro não enxerga o serviço `api`: ele derruba a
    infra, deixa o container `my_service-api` rodando e a rede termina em
    `Resource is still in use`. Passar `--profile prod` no `down` funciona
    nos dois casos — com ou sem a API no ar.

#### Profile `prod`: a API dentro do compose

O compose também traz a própria API, como um serviço `api` atrás de
`profiles: ["prod"]`. Sem o profile ele não existe — `docker compose up -d`
continua subindo só a infra, como acima. Com o profile, o `Dockerfile` do
projeto é construído e a app sobe **na rede do compose**, ao lado da infra:

```bash
cp .env.example .env
docker compose --profile prod up -d --build --wait
curl http://127.0.0.1:8000/health/readiness
```

```json
{"status":"ready","checks":{"database":true},"version":"0.1.0"}
```

Para `tempest new my_service --extras cache,tasks`, o bloco gerado é:

```yaml
services:
  api:
    # Only under `docker compose --profile prod up -d --build`;
    # plain `docker compose up -d` keeps running just the infra.
    profiles: ["prod"]
    build: .
    container_name: my_service-api
    restart: unless-stopped
    env_file: .env
    environment:
      # environment: wins over env_file. .env points every backend
      # at localhost (right for the host, wrong in a container), so
      # the hosts are re-pointed at the compose service names here.
      # SMTP_* is left to .env on purpose: MailHog is dev-only and
      # production mail goes through the real SMTP server.
      SERVER_HOST: 0.0.0.0
      SERVER_PORT: "8000"
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-app}:${POSTGRES_PASSWORD:-app}@postgres:5432/${POSTGRES_DB:-my_service}
      REDIS_URL: redis://redis:6379/0
      RABBITMQ_URL: amqp://${RABBITMQ_DEFAULT_USER:-guest}:${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/
      TASKIQ_BROKER_URL: amqp://${RABBITMQ_DEFAULT_USER:-guest}:${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    healthcheck:
      # python:3.13-slim has no curl; readiness is 503 until the DB answers.
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/readiness', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 20s
```

Pedaço por pedaço:

- **`environment:` reescreve os hosts.** O `.env` que o scaffold gera aponta
  tudo para `localhost` — certo para o processo no host, errado dentro de um
  container, onde `localhost` é o próprio container da API. O Compose dá
  precedência ao `environment:` sobre o `env_file:`, então cada host que o
  `.env.example` aponta para `localhost` é trocado pelo nome do serviço, só
  para os extras escolhidos:

    | Variável | Valor dentro do compose | Quando |
    |----------|-------------------------|--------|
    | `DATABASE_URL` | `postgresql+asyncpg://…@postgres:5432/<db>` | sempre |
    | `REDIS_URL` | `redis://redis:6379/0` | `[cache]` |
    | `RABBITMQ_URL` / `TASKIQ_BROKER_URL` | `amqp://…@rabbitmq:5672/` | `[queue]` / `[tasks]` |
    | `TASKIQ_BROKER_URL` | `redis://redis:6379/0` | `[tasks-redis]` (vence o `amqp://` do `[tasks]`) |
    | `STORAGE_ENDPOINT` | `minio:9000` | `[minio]` |

    Sem essa reescrita a API não quebra — ela sobe **no lugar errado**. A
    mesma imagem rodada só com `--env-file .env` respondeu
    `{"status":"ready","checks":{"database":true}}` com `200` e criou
    `/app/app.db` dentro do container: o `DATABASE_URL` default do `.env` é
    SQLite, e o Postgres nunca foi tocado.

- **`SERVER_HOST: 0.0.0.0` e `SERVER_PORT`.** O `Dockerfile` já define
  `SERVER_HOST=0.0.0.0`, mas o `.env` traz `SERVER_HOST=127.0.0.1` e vence
  o `ENV` da imagem — o uvicorn sobe em `http://127.0.0.1:8000` dentro do
  container e a porta publicada não responde. Fixar os dois no
  `environment:` resolve. Isso não contradiz o bind `127.0.0.1` dos
  templates: aquele vale para o processo no host; dentro do container o bind
  precisa aceitar a rede do compose.
- **`depends_on` com `condition: service_healthy`**, um por serviço de extra
  (todos têm `healthcheck`). Com `[minio]`, a API também espera o
  `minio-bootstrap` terminar (`service_completed_successfully`), para o
  bucket `uploads` existir antes do primeiro request.
- **`healthcheck` em `/health/readiness`, via `urllib`.** A
  `python:3.13-slim` não tem `curl`. O scaffold monta o
  `make_health_router`, que serve `/health/liveness` e `/health/readiness`
  — **`/health` sozinho responde `404`**. A readiness responde `503`
  enquanto o banco não atende, e o `urlopen` levanta nesse caso, então
  `healthy` significa que a app alcançou o Postgres. Ela checa **só o
  banco**: Redis, RabbitMQ e MinIO não entram no payload do scaffold.
- **`env_file: .env` é obrigatório no profile.** Sem `.env`,
  `docker compose --profile prod config` falha com
  `env file .../.env not found`; o `docker compose up -d` de dev continua
  funcionando sem ele.

!!! info "Decisões do profile `prod`"
    - **Rede default do Compose**, sem bloco `networks:`. Os nomes de
      serviço já resolvem nela; rede nomeada só faria sentido se outro
      compose precisasse se juntar a esta.
    - **Infra só em `127.0.0.1`, API em todas as interfaces.** O dev no host
      continua alcançando banco e broker; em produção eles não ficam
      expostos para fora da máquina. A porta da API é publicada
      normalmente — é ela que serve tráfego.
    - **MailHog sem profile, e o SMTP fica com o `.env`.** O MailHog sobe nos
      dois modos (o fluxo de dev não muda), mas o `environment:` da API
      **não** reescreve `SMTP_*`: dentro do container o `SMTP_HOST=localhost`
      do `.env.example` não alcança o MailHog. Em produção configure no
      `.env` o servidor SMTP real; para testar e-mail com o MailHog dentro
      do compose, use `SMTP_HOST=mailhog` no `.env`.
    - **`asyncpg` é dependência do projeto gerado.** O `pyproject.toml` do
      scaffold declara `asyncpg>=0.30.0` ao lado do `aiosqlite`, porque a
      imagem instala só o que o `pyproject.toml` declara e o profile `prod`
      sempre usa `postgresql+asyncpg://`.

##### Migrations no profile `prod`

O scaffold já vem com `alembic.ini`, `alembic/` e uma revision `baseline`
(as tabelas dos models que os extras trouxeram — `users` com `[auth]`, um
`upgrade()` vazio sem ele), e o lifespan do `src/api/app.py` gerado chama
`AlembicHelper(...).sync_schema_async()` antes do `db.connect()`. Então o
compose não precisa de serviço de migration: a API migra o Postgres ao subir.

```bash
cp .env.example .env
docker compose --profile prod up -d --build --wait
curl http://127.0.0.1:8000/health/readiness
```

Medido com `tempest new my_service --extras admin,postgres`: a API respondeu
`/health/readiness` 200, o Postgres ficou com `users` e `alembic_version` na
revision `baseline`, e reiniciar a API não rodou nenhum upgrade de novo.
O `sync_schema_async` recusa migration destrutiva (drop de tabela, coluna ou
constraint) e trata banco vazio, banco criado antes do Alembic e banco já
migrado. Com várias réplicas subindo juntas, desligue com
`DATABASE_MIGRATE_ON_STARTUP=false` e rode
`docker compose --profile prod run --rm api tempest db upgrade` num job.

Model novo ganha migration com `tempest db revision -m "..."`; a próxima
subida aplica.

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

!!! tip "Fullstack: a SPA é detectada e compilada num stage Node"
    Se o projeto tem um frontend — um `package.json` em `web/`, `frontend/`,
    `client/` ou `ui/` — o Dockerfile gerado ganha um stage Node que instala e
    compila a SPA, e só o `dist/` resultante é copiado para a imagem final.
    Nem `node_modules` nem o toolchain do Node chegam ao runtime.

    ```text
    Regenerated Dockerfile
    Regenerated .dockerignore
      SPA stage: builds web/ and copies web/dist into the image.
    ```

    | Opção | Efeito |
    | --- | --- |
    | *(nenhuma)* | Detecta pelo `package.json`. Diretório vazio não conta |
    | `--spa-dir apps-web` | Layout fora da convenção |
    | `--no-spa` | Imagem backend-only mesmo com frontend presente |

    Sirva o resultado com [`make_spa_router("web/dist")`](react-spa.md),
    incluído **depois** de todos os routers de API. Projeto sem frontend não
    ganha stage `spa` nem `COPY --from=spa`: o Dockerfile sai só com os
    stages `builder` e final mostrados abaixo.


Desde a v0.71.0, `tempest new` também gera um **`Dockerfile`** + **`.dockerignore`** prontos pra empacotar o serviço como imagem. O `Dockerfile` é **multi-stage** e usa [uv](https://docs.astral.sh/uv/):

- **stage `builder`** — instala as dependências num `/app/.venv` (camada cacheada que só re-roda quando `pyproject.toml` / `uv.lock` mudam), depois instala o projeto.
- **stage final** — copia só o venv + o código, cria `/app/logs` e passa `/app` para o usuário **não-root** (`app`, uid 1000), que então consegue escrever o `logs/` e o `app.db` do SQLite, e expõe a porta configurada.

O arquivo gerado para `my_service`, colado da saída do gerador:

```dockerfile
# Dockerfile — generated by `tempest new` for my_service.
#
# Multi-stage build using uv (https://docs.astral.sh/uv/):
#   * builder stage installs dependencies into /app/.venv
#   * final stage copies only the venv + source, runs as a non-root user
#
# Build:  docker build -t my_service .
# Run:    docker run --rm -p 8000:8000 --env-file .env my_service
# Or, with the infra:  docker compose --profile prod up -d --build
#
# The image binds SERVER_HOST=0.0.0.0 (see the final stage) so the app is
# reachable from outside the container even without a .env file. The `api`
# service in docker-compose.yaml (prod profile) builds this image and
# re-points DATABASE_URL & co. from the .env's localhost to the compose
# service names; a bare `docker run --env-file .env` does not, so it only
# reaches the infra if you edit those hosts yourself.

# ---- builder ----------------------------------------------------------------
FROM python:3.13-slim AS builder

# uv ships as a static binary; pin the tag in production for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy bytecode-compiled packages instead of symlinking — the venv must be
# self-contained when copied across stages.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (without the project) so this layer is cached
# and only re-runs when pyproject.toml / uv.lock change. uv.lock is optional
# in a fresh scaffold — the glob keeps the COPY from failing when it is absent.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY . .
RUN uv sync --no-dev

# ---- final ------------------------------------------------------------------
FROM python:3.13-slim

# Run as an unprivileged user. /app is owned by it so the app can write
# logs/ and the default SQLite app.db without root.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

# WORKDIR created /app as root before the COPY, and `COPY --chown` only sets
# ownership on the copied *contents* — not on the pre-existing /app directory
# node itself. So the app user cannot create new entries (the runtime logs/
# dir, the default SQLite app.db, etc.) inside /app and the app crashes at
# startup with `PermissionError: [Errno 13] ... 'logs'`. Chown the directory
# and pre-create logs/ so the SDK's file logging (LOG_DIR=logs) works.
RUN mkdir -p /app/logs && chown -R app:app /app

# Put the venv on PATH so `python` resolves to the project interpreter.
# SERVER_HOST=0.0.0.0 binds all interfaces inside the container so the app
# is reachable from the host (overridable via .env / `environment:`).
ENV PATH="/app/.venv/bin:$PATH" \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000

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
    aceitar conexões externas. Um `--env-file .env` sozinho **não** liga a
    imagem à infra do `docker-compose.yaml`: o `.env` aponta para
    `localhost`. Para isso use o
    [profile `prod`](#profile-prod-a-api-dentro-do-compose).

    Só que variável de `--env-file` **vence** o `ENV` da imagem: um `.env`
    copiado do `.env.example` traz `SERVER_HOST=127.0.0.1` e devolve o bind
    para dentro do container. Tire o `SERVER_HOST` do `.env` que vai para o
    container, ou ponha `SERVER_HOST=0.0.0.0` nele.

!!! tip "Subir app + infra num comando só"
    O `docker-compose.yaml` gerado já constrói esta imagem no serviço `api`,
    atrás do profile `prod`:
    `docker compose --profile prod up -d --build`. Veja o
    [profile `prod`](#profile-prod-a-api-dentro-do-compose).

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
| `[queue]` | `queue/__init__.py` (`RabbitBroker` + `AsyncQueueManager` + `get_broker`), `queue/handlers.py` (subscriber de exemplo) |
| `[tasks]` / `[tasks-redis]` | `tasks/queue.py` (`tq = TaskQueue.from_settings(settings)` + `get_task_queue`), `tasks/names.py` (`EXAMPLE_TASK_NAME`, `EXAMPLE_SCHEDULE`; não importa nada do projeto), `tasks/example.py` (só o registro: `@tq.cron(EXAMPLE_SCHEDULE, name=EXAMPLE_TASK_NAME)` chamando `scripts/example.py`), `tasks/dispatch.py` (`TaskDispatcher`: a API enfileira pelo nome, com `enqueue_by_name`), `tasks/worker.py` (importa as tasks; `broker`/`scheduler` para `taskiq worker src.tasks.worker:broker`), `tasks/__init__.py` (reexporta fila, nomes e dispatcher — nunca uma task) |

Os dois extras geram a mesma camada: o transporte sai do `TASKIQ_BROKER_URL` em runtime. Com `[tasks-redis]`, o `tempest new` também declara `TASKIQ_BROKER_URL` no `Settings` com default `redis://localhost:6379/0` e escreve a mesma URL no `.env.example`, porque o default do mixin é `amqp://`, que escolhe o RabbitMQ e exige `[tasks]`. Se mesmo assim a URL apontar para um transporte cujo extra não está instalado, o `ImportError` do `TaskQueue.from_settings` nomeia o `TASKIQ_BROKER_URL`, o valor lido (senha mascarada) e a URL do outro transporte.

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

### Subir o serviço — `tempest serve`

```bash
tempest serve                              # host/port/reload vindos das settings
tempest serve --reload                     # liga o reload nesta execução
tempest serve --host 0.0.0.0 --port 9000   # sobrescreve
tempest serve --workers 4                  # múltiplos processos (sem reload)
tempest serve --app src.api.app:create_app # app fora dos lugares convencionais
```

O `main.py` scaffoldado já sobe o uvicorn programaticamente; este comando
serve para o projeto que ainda não tem esse arquivo, ou para quando você
quer sobrescrever host/porta sem editar `.env`. A resolução é a mesma do
`run_server`: flag > settings do projeto > default do SDK
(`127.0.0.1`, `8000`, sem reload).

!!! note "O uvicorn recebe a *string*, nunca o objeto"
    O reloader reimporta o alvo no processo filho. Entregar a instância já
    importada funciona — e transforma `--reload` em nada, em silêncio. Por
    isso o comando importa o candidato uma vez só para validar, e passa
    adiante `"src.server:app"`.

`--reload` e `--workers` juntos saem com código 2: o uvicorn não honra os
dois. E `--no-reload` **desliga** de verdade, mesmo com `SERVER_RELOAD=true`
no `.env` — a flag tem três estados (ligado, desligado, "decida você").

### Console interativo — `tempest shell`

Um REPL com o projeto já carregado, para não escrever script descartável
toda vez que a pergunta é "quantos pedidos esse usuário tem?".

```bash
tempest shell
```

```text
tempest shell — Python 3.11.12, top-level await enabled
available: WidgetModel, db, select, session, settings, text
session: open on sqlite+aiosqlite:///./app.db
>>> rows = await session.execute(select(WidgetModel))
>>> rows.scalars().all()
[<WidgetModel id=...>]
```

O que está no prompt: `settings` (a instância do projeto), cada model
mapeado **que o projeto define** (a `BaseModel` do SDK fica de fora — query
na classe base nunca é o que você quis dizer), `select`, `text`, o
`AsyncDatabaseManager` como `db` e uma `session` já aberta.

!!! info "`await` no nível de cima, de propósito"
    O console compila com `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` e roda a corrotina
    **no mesmo loop** em que a sessão foi aberta. Expor um helper
    `run(coro)` pareceria equivalente e não é: sessão dirigida de um segundo
    loop levanta `MissingGreenlet` no primeiro lazy load.

`--no-db` começa sem conectar — útil quando o banco está fora do ar e você
só quer inspecionar settings e models.

### Banco de dados — `tempest db`

Wrapper Alembic. Usa o `AlembicHelper` por trás, então a configuração (`alembic.ini` + `env.py`) continua sendo a fonte da verdade.

Resolução do `DATABASE_URL` na seguinte ordem:

1. Flag `--database-url`.
2. Env var `DATABASE_URL`.
3. Settings no `core/settings.py` do projeto, sob qualquer raiz de código
   (`src` ou `app`). Vale **qualquer** instância de
   `pydantic_settings.BaseSettings` — os nomes `settings` e `config` são
   tentados primeiro, e depois o módulo é varrido por tipo —, **ou** o
   resultado de um factory zero-arg que devolva uma (`get_settings()`, ou
   qualquer callable anotado com retorno `BaseSettings`).
4. `DATABASE_URL` no `.env` do projeto.
5. `sqlalchemy.url` do `alembic.ini`.

!!! tip "O nome da instância não importa mais, e o factory vale"
    Até a 0.280.0 só o nome `settings` era aceito, e a falha era um
    `ImportError` engolido por um `except Exception: return None`. Um serviço
    que chamasse a instância de `config` recebia *"no database URL. Pass
    --database-url, set DATABASE_URL, or run inside a project with
    src/core/settings.py"* — apontando para duas condições que o projeto já
    satisfazia. Hoje o que decide é o **tipo** da instância, o **factory**
    documentado (`get_settings()`) é aceito, o `.env` é lido, e quando nada
    resolve cada tentativa reporta o motivo no stderr:

    ```text
    note: importing src.core.settings failed: ImportError("cannot import name ...")
    error: no database URL. Pass --database-url, set DATABASE_URL, put it in
           .env, or expose settings with a DATABASE_URL on
           src/core/settings.py (or app/core/settings.py).
    ```

    A mesma descoberta é compartilhada por `tempest db`, `tempest doctor`,
    `tempest user` e o `env.py` gerado — um projeto cujo settings só existe
    como `get_settings()` passa a migrar fora do CLI também.

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

!!! danger "`stamp head` não adota um banco existente"
    `stamp` grava o ponteiro sem rodar nada. Contra um schema que o Alembic
    **não** construiu, carimbar `head` registra como aplicadas revisions que
    nunca rodaram: o banco fica com as colunas velhas enquanto `current`,
    `upgrade` e `history` respondem que está em dia, e a falha aparece semanas
    depois em produção. A revision certa para adoção é a **base** — veja
    [Migrations »](migrations.md), que traz `AlembicHelper.adopt()` e
    `sync_schema()` para não escolher isso à mão.

#### Quando o Alembic recusa o comando

Erro do Alembic que descreve o **estado do banco** — e não um bug — sai como
uma linha, com o que fazer:

```console
$ tempest db revision -m "novo campo" --autogenerate
error: the database is behind head — a migration is pending. Run `tempest db upgrade`, then try again.
       (TEMPEST_DEBUG=1 for the full traceback)
$ echo $?
1
```

Quatro condições ganham conselho: banco atrás da head, histórico com mais de
uma head, revisão que não existe em `alembic/versions/`, e revisões que não
estão na mesma linhagem. Qualquer outra mensagem sai **como o Alembic
escreveu**, sem tradução — dica errada seria pior que a mensagem crua.

!!! tip "Quer o traceback de volta?"
    `TEMPEST_DEBUG=1 tempest db revision ...` re-levanta a exceção intocada.
    Use quando o erro **não** for uma das condições acima e você precisar ver
    de onde ele veio.

!!! note "Só a CLI traduz"
    Chamar `AlembicHelper.revision(...)` direto, de um script ou do lifespan,
    continua levantando `alembic.util.exc.CommandError`. Código quer a
    exceção tipada; quem está no terminal quer a frase.

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

#### Detectar drift de model — `tempest db check`

O `tempest db revision --autogenerate` grava um diff quando você pede. Nada
percebe quando alguém edita um model e **esquece** de pedir — até o deploy
que precisava da coluna.

```bash
tempest db check
```

Sem drift:

```text
No drift: the models match the migration tree.
```

Com drift, ele nomeia as operações que uma revision conteria e sai com
código 1:

```text
error: New upgrade operations detected: [('add_column', ...'colour'...)]
       Run `tempest db revision -m "<message>"` to record them.
```

!!! tip "É um passo de CI"
    Rode depois do `tempest db upgrade` no pipeline. A comparação precisa do
    banco em `head` — quando ele está atrás, o Alembic diz isso e a mensagem
    passa direto, sem virar "drift".

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

# Colunas que o SEU UserModel adiciona — repetível, uma por coluna
tempest user create --email ana@example.com --password senha-forte-12 \
    --set display_name=Ana --set locale=pt-BR

# Promove/rebaixa um usuário existente (liga/desliga is_admin)
tempest user promote --email ana@example.com    # vira admin
tempest user revoke  --email ana@example.com    # volta a ser comum

# Troca a senha de um usuário existente (pede a senha duas vezes)
# e derruba as sessões dele junto
tempest user set-password --email ana@example.com

# Tabela de refresh token fora de src.db.models
tempest user set-password --email ana@example.com \
    --refresh-token-model myapp.models:UserRefreshTokenModel

# Troca a senha e deixa as sessões vivas (opt-out explícito)
tempest user set-password --email ana@example.com --keep-sessions

# Primeira conta privilegiada já com MFA + evento de auditoria na mesma transação
tempest user create --email staff@example.com --admin --require-mfa \
    --audit-model src.db.models:AuditLogModel --event staff.bootstrapped

# Conta que perdeu o celular e os recovery codes: remove o segundo fator,
# derruba as sessões e audita na mesma transação
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel

# Lista
tempest user list                                # todos
tempest user list --admin                        # só admins
```

!!! info "Conta com MFA: `--require-mfa`, `set-password` e `mfa-reset`"
    `create --require-mfa` inscreve o segundo fator **antes** do commit:
    mostra a URI `otpauth://` e o segredo, pede o código do app
    Authenticator (até 3 tentativas) e só grava com código válido — sem ele,
    sai com código 1 e nenhuma linha é escrita. Exige `MFAMixin` no
    `UserModel` e a tabela de recovery codes (`--recovery-code-model`,
    default `src.db.models:UserRecoveryCodeModel`); os recovery codes saem
    uma vez, depois do commit. `--audit-model` (um `BaseAuditLogModel` com
    `AuditRequestMixin`) grava `record_event` na mesma transação, com o nome
    de `--event` (default `user.created`); sem ele, o comando avisa no stderr
    que nenhum evento foi gravado.

    `set-password` numa conta com MFA ativo (e `AUTH_MFA_ENABLED` ligado,
    como no login) pede o código — TOTP ou recovery code — e troca a senha
    por `UserAuthService.change_password_with_mfa`. Passo a passo na
    [receita de MFA](mfa.md#bootstrap-da-primeira-conta-privilegiada).

    Quem perdeu o celular **e** os recovery codes não tem código para dar:
    `mfa-reset` é a saída — veja
    [Recuperando quem perdeu o segundo fator](#mfa-reset) abaixo.

!!! tip "`UserModel` com coluna própria obrigatória: use `--set`"
    O `create` preenche `email`, `hashed_password`, `is_admin` e
    `is_active`. Toda **outra** coluna do seu `UserModel` entra por
    `--set <coluna>=<valor>`, validada contra as colunas mapeadas e
    convertida pro tipo da coluna (bool, int, float, `Decimal`, UUID,
    ISO-8601 de data/hora, enum pelo valor e JSON).

    ```bash
    tempest user create -e novato@example.com -p 'senha-forte-12' --no-admin
    # error: UserModel requires a value for: display_name.
    #        Pass each one as --set <column>=<value>.
    ```

    Coluna `NOT NULL` sem default que você não passou é **perguntada** num
    terminal interativo (uma pergunta por coluna) e vira erro com código 2
    numa execução sem TTY — em vez do `IntegrityError` cru que o banco
    levantava. Chave desconhecida também sai com 2, listando as colunas
    aceitas; `--set email=` / `--set hashed_password=` / `--set is_admin=`
    são recusadas apontando a flag dona (`--email`, `--password`,
    `--admin/--no-admin`).

    Insert recusado pelo banco (email duplicado, quase sempre) sai com
    código 1 e a mensagem do banco — `error: could not insert user: …` —
    em vez de traceback.

!!! tip "Sem `--admin`/`--no-admin`, o create pergunta"
    Quando você **não** passa nem `--admin` nem `--no-admin` num
    terminal interativo, o `tempest user create` pergunta
    `Should this user be an administrator? [y/N]`. Em execuções
    não-interativas (CI, pipes, scripts) o prompt é pulado e o usuário
    nasce comum (`is_admin=False`) — passe `--admin` explicitamente pra
    criar admin sem TTY.

`tempest user promote` / `tempest user revoke` localizam o usuário por email (case-insensitive) e só alternam `is_admin`. Quando nenhum usuário casa com o email, saem com código 1 e a mensagem `no user found`.

#### Trocando a senha — `tempest user set-password`

É o lado do operador do fluxo de reset: a conta pra quem não dá pra mandar link, o primeiro admin trancado fora de um ambiente novo, a credencial rotacionada depois de um incidente. Localiza por email (case-insensitive), hasheia com o `set_password` do próprio model — o mesmo hash que o login verifica — e sai com código 1 e `no user found` quando ninguém casa.

```bash
tempest user set-password --email ana@example.com
# Password:
# Confirm:
# Password updated for ana@example.com (id=6f0c...)
# Revoked 2 active refresh token(s).
```

!!! check "Trocar a senha derruba as sessões — por padrão"
    Trocar o hash, sozinho, não encerra sessão nenhuma: um access token
    já emitido continua válido até expirar, e um refresh token guardado
    no banco continua trocável por um novo — exatamente a janela que o
    reset existe pra fechar. Por isso a revogação **não é uma flag que
    você precisa lembrar**: o comando resolve
    `src.db.models:UserRefreshTokenModel` (o nome convencional da tabela
    opt-in de refresh token — o `tempest new` **não** a scaffolda; quem
    liga a tabela escreve o model com esse nome em `src/db/models/`) e revoga os refresh tokens do usuário **na mesma
    transação** que grava o hash novo.

    - Tabela em outro lugar? `--refresh-token-model myapp.models:UserRefreshTokenModel`.
      Spec que você digitou e não resolve é erro com código 2 — nunca
      degrada em silêncio pra "não revoguei nada".
    - Não quer revogar? `--keep-sessions`, que é recusado com código 2
      se vier junto de `--refresh-token-model` (as duas flags se
      contradizem).
    - Projeto que nunca ligou a tabela opt-in de refresh token não tem o
      que revogar, e o comando diz isso em `stderr`:
      `note: no refresh-token model at 'src.db.models:UserRefreshTokenModel', so no session was revoked. Pass --refresh-token-model if yours lives elsewhere.`

    A contagem é de sessões que **esta** execução matou — linha já
    revogada não é recontada, então rodar de novo imprime
    `Revoked 0 active refresh token(s).`

!!! tip "O mesmo, de dentro do código"
    `await service.revoke_user_sessions(session, user_id=user.id)` é o
    par administrativo do `revoke_refresh_token`: logout-everywhere
    endereçado por id, pra quando você não tem o token em mãos (um
    endpoint de "desconectar tudo", um agente de suporte trancando conta
    comprometida). Devolve quantas linhas revogou, e `0` quando o
    projeto não usa `refresh_token_model` — JWT stateless não tem linha
    pra derrubar.

    ```python
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def force_logout(
        user_id: UUID,
        session: AsyncSession,
        service: UserAuthService,
    ) -> int:
        """Revoga todas as sessões de um usuário. Retorna quantas caíram."""
        killed = await service.revoke_user_sessions(session, user_id=user_id)
        await session.commit()
        return killed
    ```

!!! info "A política de senha é a do seu projeto, não a da CLI"
    `create` e `set-password` validam o plaintext com
    `check_password_policy` contra os três campos `AUTH_PASSWORD_*` do
    `src.core.settings` do projeto — e contra os defaults do
    `PasswordPolicy` (mínimo 12 caracteres, máximo 72 **bytes**) quando o
    projeto não compõe `AuthSettings`. É a mesma régua do `signup` e do
    painel admin, então a senha que a CLI aceita é sempre uma que o dono
    da conta consegue usar pra logar.

    O teto é medido em **bytes UTF-8**, a unidade que o bcrypt conta: 40
    caracteres acentuados são 80 bytes e saem com código 2 e
    `error: password must be at most 72 bytes.` — antes de qualquer
    escrita no banco.

Resolução do `DATABASE_URL` igual ao `tempest db` (env var > instância ou factory de settings > `.env` > `alembic.ini`).

#### Recuperando quem perdeu o segundo fator — `tempest user mfa-reset` { #mfa-reset }

Conta com MFA ativo cujo dono perdeu o celular **e** os recovery codes: o `set-password` pede o código, e não há código para dar. O `mfa-reset` remove o fator **sem prova** — por isso pede confirmação e grava quem fez.

```bash
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel
# Remove the second factor of staff@example.com? It will log in with the password alone until it enrolls again. [y/N]: y
# MFA reset for staff@example.com (id=2ced...)
# Revoked 2 active refresh token(s).

# Fora de um terminal (CI, script): --yes é obrigatório
tempest user mfa-reset --email staff@example.com --yes \
    --audit-model src.db.models:AuditLogModel --event support.mfa_recovered

# Remove o fator e deixa as sessões vivas (opt-out explícito)
tempest user mfa-reset --email staff@example.com --yes --keep-sessions
```

- Roda `UserAuthService.mfa_reset`: limpa `totp_secret`, `totp_enabled_at` e `totp_last_step` e apaga os recovery codes (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`; sem tabela, sai com código 2 nomeando a flag).
- **Revoga as sessões por padrão**, na mesma transação, com as mesmas regras do `set-password`: `--refresh-token-model` (default `src.db.models:UserRefreshTokenModel`), `--keep-sessions` como saída explícita, e as duas juntas recusadas com código 2.
- `--audit-model` (com `AuditRequestMixin`) grava `record_event` na **mesma transação**, com o nome de `--event` (default `user.mfa_reset`); se a escrita da auditoria falha, o reset volta junto. Sem `--audit-model`, o comando avisa no stderr que nenhum evento foi gravado. `--event` sem `--audit-model` sai com código 2.
- Sem confirmação nada muda: fora de terminal, sem `--yes`, sai com código 1; no terminal, responder `n` também sai com 1.
- Conta **sem MFA ativo** sai com código 2 (`has no MFA active, nothing to reset.`); `UserModel` sem `MFAMixin` também, antes de abrir o banco. Email desconhecido sai com 1 e `no user found`.

Depois do reset a conta entra só com a senha até se inscrever de novo, e o `set-password` deixa de pedir código. A receita de MFA detalha o caminho, inclusive a chamada direta no código: [Conta que perdeu o segundo fator](mfa.md#mfa-reset).

#### Inspecionar, desativar e apagar

O `list` responde "quem existe". As quatro abaixo respondem o resto.

```bash
# A linha inteira desse usuário, incluindo as colunas que o SEU model adiciona
tempest user show --email ana@example.com
tempest user show --email ana@example.com --json

# Desliga o acesso (reversível) e derruba as sessões junto
tempest user deactivate --email ana@example.com
tempest user activate --email ana@example.com

# Sessões (refresh tokens) desse usuário
tempest user sessions --email ana@example.com
tempest user sessions --email ana@example.com --revoke

# Apaga a linha (irreversível)
tempest user delete --email ana@example.com --yes
```

`show` **redige o `hashed_password`**: nenhuma pergunta que esse comando
responde precisa do hash, e imprimi-lo coloca uma credencial no scrollback
do terminal e no log do CI.

!!! warning "`deactivate` revoga sessão, e não é opcional"
    Virar `is_active=False` não encerra nada sozinho: o refresh token que já
    foi emitido continua trocável, e a conta que você acabou de desligar
    continua rendendo access token novo. Por isso a revogação roda na **mesma
    transação** que o flag — a mesma regra que o `set-password` segue.

`sessions` sem `--revoke` lista uma linha por token (id, família, criação,
expiração, estado). Usuário sem sessão imprime tabela vazia e sai com 0 —
"não está logado em lugar nenhum" é resposta, não erro. A contagem do
`--revoke` é de sessões que **aquela execução** matou, então rodar duas
vezes imprime `Revoked 0` na segunda.

!!! danger "`delete` é irreversível, e leva os tokens junto"
    A tabela de refresh token scaffoldada tem `ForeignKey` sem
    `ON DELETE CASCADE`, então apagar o usuário com token vivo falharia com
    erro de integridade citando uma constraint em vez da causa. O comando
    apaga os tokens na mesma transação. Fora de um terminal interativo,
    `--yes` é obrigatório. Quando a intenção é "essa pessoa não deve mais
    entrar", o comando certo é `deactivate`.

### Segredos — `tempest secrets`

Quatro comandos, ordenados por quanto mexem no seu `.env`:

| Comando | Escreve no `.env`? | Quando usar |
| --- | --- | --- |
| `generate` | nunca | pegar um valor pra colar num secret manager |
| `init` | só o que está vazio ou placeholder | logo depois do `tempest new` |
| `rotate` | sempre, com backup | o segredo vazou ou envelheceu |
| `vapid` | par de chaves Web Push | ligar notificação no browser |

#### Gerar um valor avulso — `tempest secrets generate`

Não toca em arquivo nenhum: imprime e pronto.

```bash
# Um segredo, valor cru
tempest secrets generate

# Três de uma vez
tempest secrets generate --count 3

# Rotulado, pronto pra colar num .env ou num painel de deploy
tempest secrets generate --keys JWT_SECRET,TOKEN_SECRET

# JSON, pra pipar num secret manager
tempest secrets generate --keys JWT_SECRET --json
```

Saída do terceiro:

```text
JWT_SECRET=_Ptx1Cq4lUAK4EJX7GAnXMp115d6i92I_rrTDJ3sS59noFLm3N1wTWL-F0OhCkbr
TOKEN_SECRET=qNsIyp8hp4BZll_BzcDS7omceBNB4a4EI9JVWvIQEg_R5VveTY0IuLBiogp0uRBa
```

!!! tip
    `--count` e `--keys` dizem a mesma coisa (quantos segredos sair), então
    passar os dois é erro de uso, não um deles vencendo em silêncio.

#### Preencher o `.env` recém-criado — `tempest secrets init`

O `tempest new` escreve `JWT_SECRET=change-me-change-me-change-me-32` e
`TOKEN_SECRET=` vazio — exatamente os dois casos que o
`tempest check-config` reporta como `security.W001` / `security.W004`.
O `init` troca só esses:

```bash
tempest secrets init
```

```text
set JWT_SECRET (was placeholder)
set TOKEN_SECRET (was empty)
```

Chave que já tem valor de verdade é **mantida**, então rodar de novo não
faz nada:

```text
kept JWT_SECRET (already set)
kept TOKEN_SECRET (already set)
Nothing to do.
```

Isso é o que separa o `init` do `rotate`: o `init` é seguro de rodar em
qualquer momento — inclusive num script de setup — porque nunca destrói
segredo em uso. Pra substituir valor real, é opt-in explícito, e aí ele faz
backup antes:

```bash
tempest secrets init --force
```

#### Rotacionar — `tempest secrets rotate`

Gera e reescreve as linhas correspondentes no `.env` **no lugar** — fazendo
backup do arquivo antigo antes — e deixa as outras linhas intactas.

```bash
# Rotaciona JWT_SECRET e TOKEN_SECRET no .env (gera .env.bak)
tempest secrets rotate

# Só imprime os novos valores (não escreve nada)
tempest secrets rotate --print

# Chaves e arquivo customizados
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# Mais entropia, sem backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotacionar `JWT_SECRET` invalida todo token assinado com o valor antigo: usuários são deslogados e links de reset/ativação pendentes param de funcionar. Rotacione numa janela de manutenção e reinicie o serviço pra carregar os novos valores.

#### Chaves Web Push — `tempest secrets vapid`

Web Push não usa string aleatória: usa um par de chaves P-256. O browser
assina a subscrição com a pública e o servidor assina cada push com a
privada, então os dois valores nascem juntos e não saem do `generate`.

```bash
# Escreve VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY no .env
tempest secrets vapid

# Com o contato que vai no claim `sub` do JWT VAPID
tempest secrets vapid --subject mailto:ops@example.com

# Só imprime
tempest secrets vapid --print
```

São exatamente os campos que o `WebPushSettings` lê, no formato que o
`pywebpush` lê de volta: privada é o escalar de 32 bytes e pública é o
ponto não-comprimido de 65 bytes, ambos em base64url sem padding.

!!! danger
    Trocar um par que já existe **invalida toda subscrição ativa**: o browser
    que subscreveu com a chave pública antiga não aceita push assinado com a
    nova, e cada cliente precisa subscrever de novo. Por isso o comando se
    recusa a sobrescrever sem `--force`.

#### Recap

- `generate` imprime, `init` preenche o que falta, `rotate` troca tudo,
  `vapid` gera o par do Web Push.
- `init` é idempotente e nunca perde segredo em uso — é o comando do
  primeiro dia do projeto.
- `rotate` e `vapid --force` são destrutivos de formas diferentes: um
  desloga usuário, o outro derruba subscrição de push.
- Todo arquivo que esses comandos escrevem sai `0600`.

### Fila e tasks — `tempest queue` / `tempest tasks`

```bash
# Publica uma mensagem pelo broker do projeto
tempest queue publish orders.paid '{"order_id": 7}' --json
tempest queue handlers                     # canais que este serviço consome

# Tasks de fundo
tempest tasks list                         # nomes registrados
tempest tasks run src.tasks:send_welcome --arg ana@example.com --kwarg retries=2
```

O `publish` conecta, publica e **fecha** — nessa ordem, num processo só.
Fechar é o passo que o script descartável esquece, e é ele que garante o
flush da publicação antes do comando retornar.

!!! tip "O nome da task não é o nome da função"
    O TaskIQ registra sob `<módulo>:<função>` (`src.tasks:send_welcome`), então
    o nome que o `run` recebe não é o que está no arquivo. O `list` é o que diz
    qual é qual — e ele importa `<root>.tasks.jobs` antes de listar, porque
    task só existe no broker depois que o módulo que a declara foi importado.

`tasks run` **enfileira**, não executa: quem roda é o worker. Comando que
retorna com sucesso significa "a mensagem foi aceita", não "o job deu certo".

Sem `--broker` / `--queue`, os dois procuram `<root>.queue:broker` e
`<root>.tasks:tq` — os nomes que o `tempest generate --src` escreve. Quando
nada resolve, cada tentativa é listada com a causa antes do código 2.

### E-mail — `tempest email test`

O par que mais custa tempo em configuração de SMTP é STARTTLS (porta 587,
`SMTP_USE_TLS`) contra TLS implícito (porta 465, `SMTP_USE_SSL`), e o único
jeito honesto de saber qual o servidor quer é conectar e mandar.

```bash
tempest email test --to voce@example.com
tempest email test --to voce@example.com --subject "deploy ok" --body "..."
tempest email test --to voce@example.com --html      # exercita o caminho multipart
```

Monta o cliente com `EmailUtils(**settings.email_kwargs())` — a mesma
construção que o serviço faz —, então mensagem que chega aqui chega pela
app. Recusa do servidor sai com código 1 carregando a resposta dele, mais a
linha que lembra o par TLS.

Projeto que não compõe `EmailSettings` sai com código 2 dizendo isso: compor
só os mixins que se usa é o normal, não defeito.

### Object store — `tempest storage`

```bash
tempest storage check                       # endpoint responde? bucket existe?
tempest storage ls                          # chaves do bucket default
tempest storage ls relatorios/ --flat       # só o nível imediato
tempest storage put ./nota.pdf --key notas/2026-09.pdf
tempest storage get notas/2026-09.pdf --out ./baixado.pdf
tempest storage presign notas/2026-09.pdf --expires 900
tempest storage rm notas/2026-09.pdf
```

Tudo passa pelo `AsyncMinIOClient(**settings.storage_kwargs())`, então
endpoint, credencial, região e — o que mais importa — o **endpoint público**
usado para assinar URL são os do serviço.

!!! warning "URL assinada com o endpoint interno é válida e inútil"
    Assinar contra `minio:9000` produz uma URL correta que o browser do
    usuário não resolve. Configure `STORAGE_PUBLIC_ENDPOINT`; o `presign`
    imprime, no `stderr`, para qual host ele assinou, justamente para esse
    erro aparecer antes de ir para o cliente.

Bucket vazio lista nada e sai com 0 — "nenhum objeto" é resultado, não falha,
a mesma convenção dos repositories do SDK.

### Feature flags — `tempest flags`

Flag existe para ser movida sem deploy. Com o backend Redis, essa mudança
morava num `redis-cli HSET feature_flags <nome> 1` — o tipo de comando que
sai com um dígito errado na hora errada.

```bash
tempest flags list                       # tudo que o backend guarda
tempest flags enable new-checkout        # liga para todo processo que lê esse backend
tempest flags disable new-checkout
tempest flags get new-checkout           # estado guardado
tempest flags list --key ff:staging      # outro hash, outro ambiente
```

```text
new-checkout  on
legacy-upload off
```

Os quatro falam pelo `RedisFeatureFlagBackend`, então o valor que o serviço
lê e o que a CLI escreve passam pelo mesmo código de codificação.

!!! note "`off` e `unset` são respostas diferentes"
    `get` sai com **1** quando a flag não existe no backend (`unset`), e com
    **0** quando ela existe valendo `off`. A diferença importa: flag não
    definida resolve para o *default* que o chamador passou no código; flag
    em `off` sobrescreve esse default.

A URL sai de `--redis-url` > `REDIS_URL` > settings do projeto. Redis fora do
ar sai com código 2 carregando a mensagem do driver — "connection refused"
diz o que consertar, "ocorreu um erro" não.

### Cache — `tempest cache`

```bash
tempest cache ping                       # PONG, ou código 1
tempest cache stats                      # versão, chaves, memória, hit ratio
tempest cache flush --namespace orders   # invalida o que o @cached registrou
tempest cache flush --tag user:42
tempest cache flush --key "cache:orders:list:abc"
tempest cache flush --all --yes          # FLUSHDB
```

As opções direcionadas passam pelo `CacheInvalidator` — o mesmo código que o
serviço chama —, então as chaves apagadas aqui são as que o `@cached`
escreveu lá. Passe o mesmo `--key-prefix` que os decorators usam, senão os
registries não batem.

!!! danger "`--all` é `FLUSHDB`, não 'limpar o cache'"
    Apaga **toda** chave do banco selecionado: sessão, contador de rate limit
    e feature flag junto, quando dividem o mesmo Redis. Por isso exige
    `--yes`. Na dúvida, invalide por namespace.

!!! info "O hit ratio é do servidor, não da sua aplicação"
    `keyspace_hits` / `keyspace_misses` são contadores do Redis desde o último
    restart, somando todos os clientes. O Redis não atribui hit a chamador —
    o número responde "esse servidor está sendo útil", não "meu endpoint X
    está".

### Modelos — `tempest model`

Análise, benchmark, conversão e quantização de modelos ONNX. Precisa do
extra `[modelops-onnx]`, exceto `hardware`, que só reporta o que esta
máquina consegue medir:

```bash
tempest model analyze models/classify.onnx
tempest model bench models/classify.onnx --dim height=224 --dim width=224
tempest model quantize models/classify.onnx models/classify.int8.onnx
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
tempest model hardware
```

| Comando | O que faz |
| --- | --- |
| `analyze` | Parâmetros, tamanho, opset e shapes, sem executar o modelo. |
| `bench` | Latência (mediana/IQR/p95/p99), RAM, GPU e energia sobre N repetições. |
| `optimize` | Persiste as otimizações de grafo do ONNX Runtime num novo `.onnx`. |
| `quantize` | Quantização dinâmica int8. |
| `export-ort` | Converte para `.ort` + `.required_operators.config`. |
| `hardware` | O que a máquina roda e qual sampler de energia está disponível. |

`analyze`, `bench` e `hardware` aceitam `--json`, o que os torna
utilizáveis como passo de CI. Extra ausente sai com código 2 e a linha de
instalação, nunca com traceback. Detalhes em
[Modelops](modelops.md).

### Diagnóstico — `tempest doctor`

O `check-config` lê as settings e raciocina sobre elas; não abre socket
nenhum. Isso é a troca certa para um gate rápido e a errada para "por que o
serviço não sobe", cuja resposta quase sempre é uma dependência fora do ar,
inalcançável deste host, ou com credencial que ninguém atualizou.

```bash
tempest doctor
tempest doctor --json            # pra script / healthcheck de deploy
tempest doctor --timeout 2       # aperta a espera de cada sonda de rede
```

```text
OK    python         3.11.12 (/srv/app/.venv/bin/python)
OK    sdk            tempest-fastapi-sdk 0.293.0
OK    settings       Settings
OK    database       postgresql+asyncpg://app:***@db:5432/app
FAIL  redis          ConnectionError: Error 111 connecting to cache:6379
SKIP  rabbitmq       no RABBITMQ_URL
SKIP  smtp           SMTP_HOST is still the mixin default
SKIP  minio          the [minio] extra is not installed
OK    config checks  no error (2 warning(s))
```

Cada linha é uma **conexão de verdade**, não uma dedução. Sai com código 1
quando qualquer checagem falha, então serve de smoke test de deploy.

!!! warning "`skip` não é `ok`, de propósito"
    Capacidade que o projeto não configurou aparece como `skip`. Linha verde
    para um Redis que nunca foi montado é o tipo de tranquilidade que custa um
    incidente.

    O default do mixin conta como "não configurado": o `EmailSettings` nasce
    com `SMTP_HOST=localhost` e o `StorageSettings` com
    `STORAGE_ENDPOINT=localhost:9000`, então o serviço que compõe o mixin sem
    usar a capacidade pareceria configurado. A comparação é contra
    `model_fields[campo].default` — a mesma régua do `check_secrets`.

A última linha roda o registry de checagens estáticas (o mesmo do
`check-config`), então um comando responde as duas metades de "esse deploy
está sadio".

### Rotas — `tempest routes`

Lista as URLs que a aplicação **de fato** serve.

```bash
tempest routes
```

```text
METHOD  PATH                    NAME          SCHEMA  GUARDS
GET     /api/items/{item_id}    read_item     yes     require_admin
POST    /api/items/             create_item   yes     require_admin
GET     /health                 health        yes     -
GET     /painel                 painel        no      -
```

Quatro coisas nessa tabela que uma leitura de código não te dá de graça:

- **Router montado aparece.** Desde o FastAPI 0.141.1, o `include_router`
  guarda uma entrada `_IncludedRouter` em vez de achatar as rotas na
  aplicação — então `{r.path for r in app.routes}` não contém **nenhum**
  path do router incluído. O comando lê a mesma expansão que o gerador de
  OpenAPI percorre.
- **Prefixo já resolvido.** O path é o do router mais o de cada
  `include_router` acima dele.
- **Rota fora do schema também aparece**, com `SCHEMA=no` — página HTML e
  endpoint interno servem tráfego, e é justamente o que uma resposta tirada
  do `openapi.json` esconde.
- **`GUARDS` mostra a dependência herdada do include**, não só a declarada
  na rota. É como se confere que o `Depends(require_admin)` do
  `include_router` alcançou tudo que devia.

Filtros e formato:

```bash
tempest routes --match /api/orders        # só paths com esse trecho
tempest routes --method post,delete       # só esses métodos
tempest routes --all                      # inclui /docs, /redoc, /openapi.json
tempest routes --json                     # array JSON, pra script
tempest routes --app src.server:app       # app fora dos lugares convencionais
```

Sem `--app`, o comando procura o app em `src.server:app`,
`src.api.app:create_app`, `main:app` (e os mesmos sob `app/`). Quando nada
resolve, ele **lista o que tentou e por quê** antes de sair com código 2.
`--match` que não casa com nada sai com código 1, pra typo em script falhar
em vez de parecer aplicação sem rota.

### Erros documentados no OpenAPI — `tempest openapi-errors`

Compara, por rota, as `AppException` que o fluxo pode levantar com o que a rota
declarou em `error_responses(...)` / `@raises(...)`:

```bash
tempest openapi-errors                          # relatório informativo (exit 0)
tempest openapi-errors --check                  # exit 1 quando há drift (gate de CI)
tempest openapi-errors --path src --path libs   # diretórios explícitos, repetível
tempest openapi-errors --check --allow-unreachable   # só falha em undocumented
tempest openapi-errors --fix --dry-run          # diff das declarações faltantes
tempest openapi-errors --fix                    # grava (exige árvore git limpa)
```

Sem `--path`, varre `./src` ou `./app` — o que existir. A análise é estática
(`ast`, sem importar a aplicação) e percorre `router -> controller -> service ->
repository`, lendo os `raise` **e** as seções `Raises:` das docstrings.

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
1 route(s) with drift, 2 undocumented exception(s).
```

`--fix` fecha o buraco escrevendo `responses=error_responses(...)` na rota (mais
os imports que faltarem), estendendo a declaração que já existir em vez de
substituí-la. Ele só acrescenta: findings `unreachable` nunca são removidos, já
que alcançabilidade não enxerga raise dinâmico.

Detalhes e limitações na receita [Erros no OpenAPI »](openapi-errors.md).

---

### Guards de permissão — `tempest permissions`

Lê o contrato dos guards do `@requires` direto do código-fonte (`ast`, sem
importar a aplicação) e reporta o que o decorator não consegue ver em tempo de
import:

```bash
tempest permissions                    # relatório informativo (exit 0)
tempest permissions --check            # exit 1 se houver erro (gate de CI)
tempest permissions --check --strict   # falha também nos warnings
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
2 finding(s), 1 error(s).
```

Erros: guard que levanta fora da hierarquia `AppException`, guard-predicado
(`-> bool`), aridade errada, guard `async` em função sync, rota sem parâmetro de
usuário. Warnings: guard que nunca nega, anotação faltando, guard que o checker
não conseguiu resolver (lambda, nome duplicado, definição fora do escopo).

Detalhes na receita [Guards de permissão (`@requires`) »](permission-guards.md).

---

### Integrações — `tempest integrations`

```bash
tempest integrations list                    # o que o SDK ships + estado da credencial aqui
tempest integrations verify openpix          # uma chamada autenticada de verdade
tempest integrations verify mercado-pago
tempest integrations verify zap --base-url http://localhost:3000
```

Num projeto recém-saído do `tempest new`, sem credencial nenhuma no `.env`:

```text
openpix       tempest_fastapi_sdk.integrations.payment.openpix:OpenPixClient
              OPENPIX_APP_ID not set
mercado-pago  tempest_fastapi_sdk.integrations.payment.mercado_pago:MercadoPagoClient
              MERCADOPAGO_ACCESS_TOKEN not set
stripe        tempest_fastapi_sdk.integrations.payment.stripe:StripeClient
              no settings mixin (build the client yourself)
zap           tempest_fastapi_sdk.integrations.messaging.zap:ZapClient
              no settings mixin (build the client yourself)
```

O `verify` monta o cliente com `HTTPClient(**settings.<provedor>_kwargs())` e
faz **a leitura autenticada mais barata** que cada API oferece: `GET /company`
na OpenPix e `GET /users/me` no Mercado Pago — nenhuma das duas move dinheiro
nem cria registro. Credencial errada sai com código 1 carregando a recusa do
provedor.

!!! note "Esse endpoint é tabela mantida à mão — com guard"
    Escolher "a chamada mais barata" de cada provedor é decisão humana, e
    nome de endpoint gerado muda. Um teste assere que cada método nomeado
    ainda existe no cliente gerado **e** que não exige argumento, então
    renomeação upstream quebra um teste em vez do terminal de quem está de
    plantão.

Os dois provedores sem mixin de settings no SDK se comportam diferente:

- **zap** precisa de `--base-url`, porque o SDK não sabe onde a sua
  instância mora. O `verify` chama `health`, que não pede credencial, então
  ele mede **alcance**, não autorização. Sem `--base-url` sai com código 2;
  instância fora do ar sai com código 1:

    ```text
    error: zap has no settings mixin, so the CLI does not know where it lives. Pass --base-url.
    error: zap refused the call — ConnectTimeout:
    ```

- **stripe** não tem `verify`: a API não oferece leitura autenticada que não
  gaste dinheiro nem crie registro. O comando sai com código 2, com ou sem
  `--base-url`:

    ```text
    error: stripe exposes no cheap authenticated read, so there is nothing the CLI can call without spending money or creating a record. Construct the client in your service.
    ```

### Agentes — `tempest agents`

```bash
tempest agents tools --agent src.agents:agent     # que ferramentas ele tem
tempest agents run "resuma o relatório" --agent src.agents:agent --trace
```

`tools` não chama modelo nenhum — nenhum token é gasto, e funciona com
backend sem credencial configurada. É a resposta rápida para "essa
configuração de agente enxerga mesmo a ferramenta que acabei de registrar?".

!!! warning "O código de saída segue `succeeded`, não 'não levantou exceção'"
    Run cortado pelo orçamento (passos ou tempo) **ainda devolve texto**.
    Tratar esse texto como resposta é o erro que o comando se recusa a
    cometer por você: `run` sai com 1 e imprime o motivo da parada
    (`max_steps`, `max_seconds`) no `stderr`, com a saída no `stdout` do
    mesmo jeito.

Não existe layer `agents` scaffoldada, então `--agent` é praticamente sempre
necessário — o comando diz isso em vez de fingir uma convenção.

### Cliente de integração — `tempest openapi-client`

Gera schemas Pydantic + um cliente HTTP tipado a partir da especificação OpenAPI
de um sistema de terceiros:

```bash
tempest openapi-client https://api.terceiro.com/openapi.json --name terceiro
tempest openapi-client ./vendor/spec.yaml --name terceiro --force
tempest openapi-client <spec> -H "Authorization: Bearer $TOKEN" --schemas-only
```

Escreve `<src|app>/integrations/<name>/` com `schemas.py` (uma classe por
componente, com `title`/`description`/`examples` da spec preenchidos) e
`client.py` (um método `async` por operação, sobre um `HTTPClient` injetado).

```text
  + src/integrations/terceiro/__init__.py
  + src/integrations/terceiro/client.py
  + src/integrations/terceiro/schemas.py
4 schema(s), 12 operation(s).
```

Detalhes, cobertura de OpenAPI e limitações na receita
[Cliente de integração (OpenAPI) »](openapi-client.md).

---

### Exportar o contrato — `tempest openapi-export`

O `openapi-client` consome a spec de outra pessoa; este escreve a **sua**.

```bash
# Documento no stdout
tempest openapi-export

# Arquivo (JSON por padrão)
tempest openapi-export --out contracts/openapi.json

# YAML (precisa do extra [openapi])
tempest openapi-export --out contracts/openapi.yaml --yaml
```

Duas coisas saem disso. A primeira é alimentar um gerador — o próprio
`tempest openapi-client`, um gerador de cliente TypeScript, um mock server —
**sem subir o serviço**. A segunda é commitar o documento e transformar
mudança de contrato em algo que o revisor vê:

```bash
tempest openapi-export --out contracts/openapi.json --check
```

```text
error: contracts/openapi.json is out of date:
  + DELETE /api/items/{item_id}
  - GET /api/legacy
  ~ /api/items/{item_id} changed
Run 'tempest openapi-export --out <file>' to refresh it.
```

Código 1 quando difere, 0 quando está em dia. Operação que entrou e que saiu
vêm primeiro porque são as que quebram consumidor; mudança dentro de uma
operação vira `~ <path> changed`. "A spec mudou" não é mensagem revisável —
por isso o comando nomeia o que se moveu.

!!! tip "No CI"
    Rode `--check` no mesmo job que roda os testes. O dia em que alguém
    muda um status code, um campo obrigatório ou um path, o PR fica
    vermelho com a linha exata, em vez de o cliente descobrir em produção.

### Descrição de PR com IA — `tempest pr-prompt`

A branch ficou verde e falta a parte que todo mundo pula: escrever a descrição
do Pull Request. Qualquer assistente escreve uma boa — o que falta pra ele são
as duas coisas que moram no repositório: o **template** que o time combinou e o
**diff** que a branch realmente produziu.

`tempest pr-prompt` monta os dois num prompt só e joga em `stdout`, então ele
pipa direto pra IA que você usa:

```bash
tempest pr-prompt                               # compara com main, prompt no stdout
tempest pr-prompt develop                       # outra base
tempest pr-prompt | claude -p                   # pipa direto pro assistente
tempest pr-prompt --out pr_prompt.txt           # grava num arquivo pra colar
```

O prompt carrega três blocos:

1. **O template do PR.** O do próprio repositório vence — é o contrato que os
   revisores daquele projeto leem. São procurados, nessa ordem:
   `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
   `.github/PULL_REQUEST_TEMPLATE/pull_request_template.md`,
   `.gitlab/merge_request_templates/default.md`, `docs/pull_request_template.md`,
   `.pull_request_template.md` e `pull_request_template.md`. Sem nenhum deles,
   entra o template embutido do SDK.
2. **As regras** que impedem a IA de devolver o template com os placeholders
   dentro: nada de `Sim/Não` sem escolher, nada de `_texto em itálico_` de
   instrução, nenhuma seção apagada, e nada de inventar migration, env var ou
   dependência que não aparece no diff.
3. **O contexto da branch**: assuntos dos commits, a lista `--name-status` dos
   arquivos alterados e um trecho do patch de cada arquivo.

!!! info "O diff é o mesmo que a forge mostra"
    Os patches são lidos como `base...head` (três pontos) — o diff em relação
    ao *merge-base*, que é o que o GitHub/GitLab exibe no PR. Com dois pontos,
    todo commit que entrou na `base` depois que sua branch nasceu seria
    atribuído a você.

!!! info "O que é completo e o que é amostrado"
    A lista de **commits** e a lista de **arquivos alterados** vão inteiras,
    sempre — a IA sempre sabe *o que* mudou. O que é limitado são os **trechos
    de diff**: por padrão os 10 arquivos com mais linhas alteradas, cada um
    cortado em 1500 caracteres. Ou seja, o que é amostrado é *como* mudou.

Os trechos são limitados porque o diff inteiro de uma branch grande não cabe
no contexto e é quase todo ruído (lock file, changelog, migration gerada). Os
arquivos entram **ordenados por linhas alteradas**, não em ordem alfabética —
senão o orçamento vai embora em `.github/` e `CHANGELOG.md` antes de chegar no
arquivo sobre o qual o PR é:

```bash
tempest pr-prompt --full                        # todos os arquivos, patch inteiro
tempest pr-prompt --max-files 20                # 20 arquivos com patch (default: 10)
tempest pr-prompt --max-files 0                 # só a lista de arquivos, sem patch
tempest pr-prompt --max-chars 4000              # mais patch por arquivo (default: 1500)
```

`--full` levanta os dois limites de uma vez — use numa branch pequena o
bastante pra mandar inteira. Ele **recusa** vir junto com `--max-files` /
`--max-chars` (saída `2`): sobrescrever em silêncio um número que você digitou
seria pior que reclamar.

!!! check "Nada é cortado em silêncio"
    Um patch truncado leva a marca `trecho cortado` e os arquivos que ficaram
    sem trecho viram uma linha explícita no prompt (`Mais N arquivo(s)
    alterado(s)…`). A IA lê um contexto parcial como parcial, em vez de tratar
    o pedaço como a mudança inteira. O corte também respeita a quebra de linha,
    pra não sobrar meia linha de diff — que a IA leria como código que não
    existe.

O resumo (template escolhido, contagem de commits/arquivos) sai em `stderr`,
então o pipe continua limpo:

```text
template: .github/pull_request_template.md
7 commit(s), 12 changed file(s), 10 excerpt(s), 2 file(s) without a patch.
```

Outras opções:

```bash
tempest pr-prompt --head feat/outra             # descreve outra branch, sem checkout
tempest pr-prompt --lang en                     # regras e template embutido em inglês
tempest pr-prompt -t docs/meu_template.md       # template explícito, vence todos
tempest pr-prompt -p ../outro-repo              # roda contra outro repositório
```

!!! tip "Base inexistente vira `origin/<base>`"
    Num clone recém-feito costuma não existir uma `main` local, só
    `origin/main`. Quando a base não resolve, o comando tenta
    `origin/<base>` antes de falhar. Se nem isso existir, ele sai com código
    `2` e diz qual ref não encontrou. Quando a comparação não tem nem commit
    nem arquivo, a saída é código `1` — quase sempre a base está errada.

---

### Gates de qualidade

!!! info "Esses comandos vêm do `tempest-cli` (v0.226.0)"
    O gate de qualidade não tem nada de FastAPI — é `ruff`, `mypy` e
    `pytest`. Desde a v0.226.0 ele vive num pacote próprio,
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), que o extra
    `[cli]` do SDK declara e que o SDK monta na CLI dele.

    **Nada muda para você**: `tempest check` é o mesmo comando, com as
    mesmas flags e o mesmo `[tool.tempest] typing_strictness`. O que
    muda é que quem **não** usa FastAPI agora consegue instalar só o
    gate:

    ```bash
    uv add --dev tempest-cli
    tempest-cli check
    ```

    Por que separar: para chegar nesses quatro comandos era preciso
    instalar FastAPI, SQLAlchemy, Alembic e Pydantic — 38,7 MB de
    dependências e cerca de 0,5 s de import por invocação, medidos, para
    comandos que não tocam em nada disso.

    Os dois nunca divergem porque há uma implementação só: o SDK chama
    `register_commands(app)` do `tempest_cli`. Importar
    `tempest_fastapi_sdk.cli.lint` ou `.pr_prompt` continua funcionando
    e devolve as mesmas funções.

Os comandos de lint chamam a ferramenta do projeto, procurando o executável nesta ordem:

1. o `$VIRTUAL_ENV` ativado;
2. o `.venv` mais próximo, subindo a partir do diretório atual — o ambiente do projeto, mesmo sem ativação;
3. o diretório do interpretador que roda o próprio `tempest`;
4. o `PATH`, pulando shim de gerenciador de versão (pyenv/asdf) que não despacha para lugar nenhum;
5. `uv run --with <tool> <tool>`, quando o `uv` está no `PATH`.

O projeto vem antes do ambiente da CLI de propósito: com `uv tool install`, a CLI tem o próprio `ruff`, e um projeto que fixa outra versão continua rodando a que fixou.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix-only . + ruff format . + ruff check .   (escreve)
tempest fix --unsafe                            # também aplica os --unsafe-fixes do ruff
tempest format                                  # ruff format .          (escreve)
tempest fmt-check                               # ruff format --check .   (somente leitura)
tempest type                                    # mypy .
tempest test                                    # pytest -n auto -p no:cacheprovider  (paralelo, o padrão)
tempest test tests/api/                         # com filtro de caminho
tempest test -w 4                               # quatro workers
tempest test --serial                           # em série (--lf / --ff, depurador)
tempest check                                   # lint + fmt-check + type + lint-layers + test, para no primeiro erro
tempest check --rule IMP002 --strict            # o mesmo gate, com o lint-layers só nessa regra e aviso derrubando
```

`test` e `check` rodam a suíte em paralelo por padrão desde o `tempest-cli`
0.6.0 (o extra `[cli]` declara `tempest-cli>=0.7.0`); sem o pytest-xdist no
ambiente do pytest, avisam e rodam em série. Quando usar, o banco por worker
e o que fazer com teste que só falha em paralelo estão na
[receita de testes](testing.md#a-suite-em-paralelo-o-padrao-do-tempest-test).

`tempest fix` é a passada única de "organize o projeto" — ordena e remove imports duplicados, descarta imports não usados, normaliza aspas de strings, remove espaços em branco no fim das linhas e então roda `ruff format` para alinhar indentação, comprimento de linha, linhas em branco e a quebra de linha final. Rode-o antes do push quando o CI fica pegando detalhes de estilo.

!!! info "O relatório sai do arquivo já formatado (`tempest-cli` 0.5.1)"
    O `tempest fix` roda três passadas: `ruff check --fix-only`,
    `ruff format` e, por fim, `ruff check`, que é de onde saem o relatório
    e o exit code. Uma violação que o formatter resolveu não aparece mais
    nem derruba o comando. O que sobra depois da formatação continua saindo
    com código ≠ 0, então o CI segue falhando nas pendências reais.

!!! tip "Comentários e strings longos são encurtados, com prova de AST (`tempest-cli` 0.5.1)"
    O `ruff format` não quebra comentário nem string literal — igual ao
    Black. Depois dele, o `tempest fix` quebra cada comentário `E501` no
    limite (um comentário no fim da linha sobe para cima dela) e divide cada
    string literal simples nos espaços, em partes concatenadas dentro de
    parênteses. Cada edição só fica quando a AST do arquivo é idêntica antes
    e depois, então o programa não muda. Com `line-length = 88`, isto:

    ```python
    """Long lines."""

    MESSAGE: str = "this is a deliberately long plain string literal that goes well beyond the configured line length limit"


    def f() -> int:
        """Return one."""
        return 1  # this trailing comment is deliberately long so that it overflows the configured line length
    ```

    vira isto:

    ```python
    """Long lines."""

    MESSAGE: str = (
        "this is a deliberately long plain string literal that goes well beyond the "
        "configured line length limit"
    )


    def f() -> int:
        """Return one."""
        # this trailing comment is deliberately long so that it overflows the configured
        # line length
        return 1
    ```

    Pragma de ferramenta (`noqa`, `type: ignore`, `fmt:`, …), f-string,
    string com aspas triplas e palavra maior que uma linha ficam como estão.
    Para essas linhas — e para o que o formatter não tem onde quebrar, como
    um nome pontilhado — o `tempest fix` nomeia o token que estoura, o tipo
    e a ação que resolve, e separa as pendências entre as que não têm
    autofix e as que o `tempest fix --unsafe` corrigiria. Uma f-string
    longa, por exemplo, sai assim (saída 1):

    ```text
    1 line(s) still too long after ruff format — the line-length setting was applied; the formatter has nowhere to break these:
      src/longs.py:4 [string] ` this is a deliberately long f-string literal that goes well beyond the limit`
        ruff format does not split string literals, and fix could not split this one safely (an f-string, a triple-quoted string, or a word longer than a line). Break it into implicitly concatenated parts inside parentheses.
    1 residual violation(s): 1 with no autofix (edit by hand).
    ```

Todo comando retorna o exit code da ferramenta subjacente, então `tempest check` é seguro para conectar ao CI (`tempest check || exit 1`) ou a hooks de pre-commit. Quando nenhum dos cinco passos acha a ferramenta, o wrapper sai com `127` em vez de falhar silenciosamente, dizendo o que instalar:

```text
error: 'ruff' was not found in this project's environment, on PATH, and 'uv' is unavailable to run it. Install it with 'uv add --dev ruff' — or the whole set with 'uv add --dev "tempest-cli[tools]"' — and retry.
```

### Regras de camada — `tempest lint-layers`

O `CLAUDE.md` que o `tempest new` escreve diz como um service e um
repository têm forma. Prosa não é gate: um serviço com dez services sem
`BaseService`, repositories embrulhando o `list` do SDK e funções soltas
num `services/feeds.py` passou `ruff`, `mypy` e `pytest` limpos (#520).
O `tempest lint-layers` é o gate dessas regras (e da convenção de nome de enum) — e o `tempest check` o roda
como passo próprio, depois do `mypy` e antes do `pytest`:

```bash
tempest lint-layers            # o projeto do diretório atual (lê src/ ou app/)
tempest lint-layers src        # o diretório de código direto
```

Ele lê o código com `ast` e **nunca importa o projeto**, então roda mesmo
com import quebrado. Cada violação é uma linha `arquivo:linha: CÓDIGO
mensagem`, e a mensagem diz o que mudar:

| Código | Onde | O que acusa |
| --- | --- | --- |
| `SVC001` | `services/` | classe pública que não herda `BaseService` (direto, via outro service do projeto ou via um `*Service` importado do `tempest_fastapi_sdk`) |
| `SVC002` | `services/` | segunda classe pública no módulo, ou qualquer função de módulo — helper vai para `utils/` |
| `SVC003` | `services/` | `__init__` que só chama `super().__init__` com os próprios parâmetros e/ou guarda `self.repository` |
| `REPO001` | `repositories/` | método cujo corpo é só `return [await] self.<método do BaseRepository>(...)` |
| `ENUM001` | todo o código | classe pública que herda `BaseStrEnum` (direto ou via outra classe do projeto) e cujo nome não termina em `Enum` — a mensagem diz o nome esperado; renomear não pede migration, porque o `enum_type_name` tira um `Enum` final (#518) |
| `RTR001` | `routers/` | chamada a uma classe `*Controller` ou `*Service` (nome com inicial maiúscula) em qualquer ponto do módulo — corpo de rota, função auxiliar, nível de módulo. A construção vai para um provider em `api/dependencies/controllers.py` e chega à rota por `Depends` |
| `RTR002` | `routers/` | rota (`@<router>.get` / `post` / `put` / `patch` / `delete` / `head` / `options` / `trace` / `api_route`) cuja anotação de retorno ou `response_model=` cita um model ORM, em qualquer profundidade (`list[ProductModel]`, `Optional[ProductModel]`, união com `None`) |

Os três casos do #520, num projeto gerado pelo `tempest new`, saem assim
no `tempest check` (trecho da saída real; exit code `1`):

```text
$ mypy --disallow-untyped-defs --disallow-incomplete-defs .
Success: no issues found in 29 source files
$ lint-layers
src/db/repositories/news.py:18: REPO001 `NewsRepository.list_all` only forwards to BaseRepository.list; delete it and call `repository.list(...)` with those arguments at the call site.
src/services/feeds.py:9: SVC002 module-level function `page_envelope` in services/; move it to utils/, or make it a method of the service that uses it.
src/services/feeds.py:24: SVC001 `NewsService` does not inherit BaseService; subclass `BaseService[Repository, ResponseSchema]` and drop what it reimplements. A service with no repository opts out with `# tempest: no-base-service` on the class line; a type that is not a service belongs in schemas/ or core/.
src/services/feeds.py:27: SVC003 `NewsService.__init__` only stores the repository, which BaseService already does; delete it.
src/services/feeds.py:37: SVC002 `TrendService` shares this module with `NewsService`; one public class per module in services/ — move it to its own file.
14 layer finding(s).
```

O projeto como o `tempest new` gera passa limpo, e continua limpo com os
exemplos de `ProductService` / `ProductRepository` / router de produto do
`CLAUDE.md` copiados para dentro dele — o teste da suíte faz exatamente
isso.

!!! tip "Service sem repository: opt-out explícito"
    Um service que não tem repository para entregar ao `BaseService` (um
    catálogo estático lido de arquivo, uma fachada sobre API externa)
    marca a linha do `class` com `# tempest: no-base-service`. O marcador
    tira a classe do `SVC001` e do `SVC003`; o `SVC002` continua valendo.

    ```python
    # src/services/catalogue.py
    import json
    from pathlib import Path


    class CatalogueService:  # tempest: no-base-service
        """Catálogo estático lido de um JSON — não há repository."""

        def __init__(self, path: Path) -> None:
            self.items: list[dict[str, str]] = json.loads(path.read_text())
    ```

    É um comentário, não um atributo de classe, porque é uma instrução para
    o checker e nada em runtime lê — o mesmo motivo de `# noqa` ser
    comentário.

!!! info "O que o `REPO001` deixa passar de propósito"
    Um método que **sobrescreve** um nome do `BaseRepository` (`delete`
    encaminhando para `soft_delete`) é política, não wrapper: quem chama
    `service.delete` passa a apagar logicamente. E um método que chama
    outro método definido na própria classe não é wrapper do SDK.

#### Router: controller por `Depends`, resposta em schema

As duas regras de `routers/` cobrem os desvios que mais aparecem num
router: montar o controller na mão dentro da rota e devolver a linha ORM.
Este router, escrito num projeto do `tempest new`, faz os dois:

```python
# src/api/routers/product.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.resources import get_session
from src.controllers import ProductController
from src.db.models import ProductModel
from src.db.repositories import ProductRepository
from src.services import ProductService

router: APIRouter = APIRouter(prefix="/products", tags=["products"])


@router.get("/{product_id}")
async def get_product(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductModel:
    """Read a product."""
    controller = ProductController(ProductService(ProductRepository(session)))
    return await controller.get_by_id(product_id)
```

O `tempest lint-layers` sai com exit code `1` e três linhas (saída real):

```text
src/api/routers/product.py:15: RTR002 route `get_product` returns the ORM model `ProductModel` (return annotation); return the response schema the repository maps it to (`map_to_response`) and annotate the route with that schema.
src/api/routers/product.py:20: RTR001 `ProductController(...)` is built inside a router; move the construction to a provider in api/dependencies/controllers.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
src/api/routers/product.py:20: RTR001 `ProductService(...)` is built inside a router; move the construction to a provider in api/dependencies/services.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
3 layer finding(s).
```

A correção é a forma que o `CLAUDE.md` do projeto ensina: o provider
`get_product_controller` em `src/api/dependencies/controllers.py` monta a
cadeia, a rota recebe `controller: ProductController =
Depends(get_product_controller)` e anota o retorno com
`ProductResponseSchema`.

**O que conta como model ORM.** Um nome importado de um módulo cujo
caminho tem o segmento `models` (`src.db.models`, `src.db.models.user`),
ou uma classe pública definida num diretório `models/` do projeto — desde
que a rota não a importe de um módulo `schemas`. Um schema que por acaso
tem o mesmo nome de um model continua limpo.

!!! tip "Construção que precisa ficar no router: `# tempest: router-builds`"
    O marcador é a exceção que você assume por escrito, para a review ler
    o motivo. Ele vale para as chamadas daquela linha, não para o módulo
    inteiro:

    ```python
    # src/api/routers/reports.py
    from fastapi import APIRouter

    from src.services import ReportService

    report_service: ReportService = ReportService()  # tempest: router-builds
    router: APIRouter = APIRouter(prefix="/reports")
    ```

    O `RTR002` não tem opt-out: a correção tem uma forma só — devolver o
    schema que o `map_to_response` do repository produz.

!!! warning "O que as regras de router não veem"
    Elas leem sintaxe e nunca importam o projeto, então ficam de fora:
    rota sem anotação de retorno e sem `response_model=`; model guardado
    fora de qualquer `models/` e importado de módulo sem esse segmento; as
    bases ORM do próprio SDK (`BaseUserModel`) usadas direto na rota;
    controller montado por alias (`factory = ProductController; factory()`)
    ou classe cujo nome não termina em `Controller` / `Service`.

    **Onde o `@requires` fica não é regra.** A receita de
    [guards de permissão](permission-guards.md) aplica o `@requires` na
    rota como primeira forma, e o próprio `requires` documenta handler de
    rota, método de controller e de service como lugares válidos. Acusar o
    `@requires` na rota acusaria a documentação do SDK; a escolha entre os
    dois lugares é revisão — o critério está em
    [Arquitetura › Guards](../architecture.md#guards-dependencia-no-router-ou-requires-no-metodo).

!!! note "Requer `tempest-cli>=0.7.0`"
    O passo entra no `tempest check` pelo `extra_steps` do
    `register_commands` (`tempest-cli` 0.5.0), e o `--rule` / `--strict` do
    `check` chega a ele pelo `run_with_options` (`tempest-cli` 0.7.0); o
    extra `[cli]` declara `tempest-cli>=0.7.0`, então ele já vem. O
    `tempest-cli check` sozinho, sem o SDK, não conhece estas regras: elas são
    do SDK, e ali o `--rule` sai com exit code `2`.

#### Camada em pacote de contexto (aviso)

Três regras cobram a forma das quatro camadas — `controllers/`,
`services/`, `repositories/` e `models/`. Entram como **aviso**:

| Código | Onde | O que acusa |
| --- | --- | --- |
| `LAY001` | `controllers/`, `repositories/`, `models/` | segunda classe no módulo (qualquer nome, privada também) ou função de módulo. Em `services/` quem acusa é o `SVC002`, como erro |
| `LAY002` | as quatro camadas | módulo direto na raiz da camada: `models/route.py` vai para `models/<contexto>/route.py`. Qualquer nome de contexto vale; `__init__.py` fica de fora |
| `REPO002` | `repositories/` | import de um nome `*Repository` de outro módulo de repository (import absoluto com segmento `repositories`, ou relativo). Nome usado só como base de classe — o repository base do projeto — passa |

Um `models/route.py` com dois models e um repository que importa outro
saem assim (saída real, exit code `0`, porque são só avisos):

```text
src/db/models/route.py:1: LAY002 (warning) models/route.py sits at the root of the layer; move it to models/<context>/route.py (a subpackage per domain context, re-exported by models/__init__.py).
src/db/models/route.py:5: LAY001 (warning) `StopModel` shares this module with `RouteModel`; one class per module in models/ — move it to its own file in the same context package.
src/db/repositories/orders/order.py:1: REPO002 (warning) imports `CustomerRepository` from another repository; a repository reads its own tables (join in the query) and a service combines two repositories.
3 layer finding(s): 0 error(s), 3 warning(s).
```

!!! check "O projeto do `tempest new` passa em todas"
    O scaffold já sai em pacotes de contexto (`controllers/system/`,
    `db/models/users/`), com `__init__` relativo, `/api` só no
    `build_api_router` e testes com `Cenário:`/`Esperado:`: o
    `tempest lint-layers` não acusa nada, nem aviso (medido sem extras, com
    `auth,queue,tasks,ssr` e com `admin,email,minio,cache,tasks-redis`; o
    `tests/cli/test_layer_lint.py` fixa os três).

#### O que service e controller recebem (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `DI001` | `services/`, `controllers/` | parâmetro do `__init__` anotado com `AsyncSession` (em qualquer profundidade: união com `None`, anotação string). A sessão chega ao repository; o service recebe o repository |
| `DI002` | `services/`, `controllers/` | `__init__` de service recebendo um `*Service`, ou de controller recebendo um `*Controller`. Juntar dois services é trabalho do controller |
| `DI003` | todo o código | função ou método chamado `from_session`. A montagem fica no provider de `api/dependencies/` (dentro da requisição) ou numa função `build_*` do composition root (fora dela) |

Este service tem as três formas (`tempest lint-layers --rule DI001 --rule
DI002 --rule DI003`, saída real):

```python
# src/services/wallet/settlement.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.wallet.wallet import WalletService


class SettlementService:
    """Liquida o saldo retido."""

    def __init__(self, session: AsyncSession, wallet_service: WalletService) -> None:
        """Guarda a sessão e o service da carteira."""
        self.session = session
        self.wallet_service = wallet_service

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SettlementService":
        """Monta o service sobre uma sessão."""
        return cls(session, WalletService(session))
```

```text
src/services/wallet/settlement.py:9: DI001 (warning) `SettlementService.__init__` takes the session (`session`); take the repository instead and let the provider in api/dependencies/ build it from the session.
src/services/wallet/settlement.py:9: DI002 (warning) `SettlementService.__init__` takes `WalletService` (`wallet_service`), a service of the same layer; combine the two one layer up, or declare the exception with its reason.
src/services/wallet/settlement.py:15: DI003 (warning) `from_session` assembles the graph inside the class; build it in a provider of api/dependencies/ for a request, or in a `build_*` function of the composition root outside one.
3 layer finding(s): 0 error(s), 3 warning(s).
```

Quando dois services precisam mesmo andar juntos — no transport, a
liquidação e o crédito na carteira tinham de cair na mesma transação —, o
caminho é a exceção declarada, mirando só aquele parâmetro:

```python
ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "DI002 src/services/wallet/settlement.py::SettlementService.wallet_service": (
        "a liquidação e o crédito caem na mesma transação"
    ),
}
```

!!! tip "Montagem fora da requisição"
    Task, consumer, agente e seed não têm `Depends`. Em vez de cada classe
    ganhar um `from_session`, junte as funções `build_*` num composition
    root e reutilize-o em todos esses lugares e na suíte. A receita de
    [agentes com banco](agents-db.md) mostra a forma.

#### Função aninhada e helper privado (aviso)

Estas duas regras leem o código **e a suíte** (`tests/` ao lado do
`src/`). No transport, a maior parte das ocorrências estava nos testes.

| Código | O que acusa |
| --- | --- |
| `STR001` | função definida dentro de outra função — inclusive dentro de método ou de classe local. Callback que guarda estado vira classe com `__call__`; o que só fixa um argumento vira `functools.partial` sobre uma função de módulo |
| `STR002` | função de módulo cujo nome começa com um `_` só (`__getattr__` e métodos passam). Vira método da classe que a usa, função pública em `utils/` ou, na suíte, fixture ou módulo de helper público |

Com estes dois arquivos (saída real):

```python
# src/utils/retry.py
from collections.abc import Awaitable, Callable


def with_retry(attempts: int) -> Callable[[], Awaitable[None]]:
    """Repete a chamada até dar certo."""

    async def wrapper() -> None:
        """Tenta de novo."""

    return wrapper
```

```python
# tests/test_app.py
def _make_payload() -> dict[str, str]:
    """Monta o corpo da requisição."""
    return {"name": "Picos"}
```

```text
src/utils/retry.py:7: STR001 (warning) `with_retry.wrapper` is defined inside a function; a callback that keeps state becomes a class with `__call__`, and one that binds an argument goes through functools.partial over a module-level function.
tests/test_app.py:1: STR002 (warning) module-level `_make_payload` is a helper with no home; make it a method of the class that uses it or a public function in utils/ (in tests: a fixture or a public helper module).
2 layer finding(s): 0 error(s), 2 warning(s).
```

#### Import entre pacotes do projeto (aviso)

| Código | O que acusa |
| --- | --- |
| `IMP001` | `__init__.py` importando módulo do próprio pacote com import absoluto. Vizinho se importa relativo: `from .orders import OrderService` |
| `IMP002` | fora de `__init__.py`, import de um nome pelo submódulo onde ele mora quando um pacote acima já o reexporta. O reexport é resolvido de forma transitiva, inclusive por `from .x import *`, e a sugestão é o pacote mais alto que reexporta o nome **sem conter** o módulo que importa — um service nunca é mandado importar o `__init__` da própria camada |

Os dois leem o código e a suíte. Com estes quatro arquivos
(`tempest lint-layers --rule IMP001 --rule IMP002`, saída real):

```python
# src/services/__init__.py
from src.services.orders import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/__init__.py
from .order import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/order.py
class OrderService:
    """Pedidos."""
```

```python
# src/controllers/orders/order.py
from src.services.orders.order import OrderService


class OrderController:
    """Orquestra pedidos."""

    def __init__(self, service: OrderService) -> None:
        """Guarda o service."""
        self.service = service
```

```text
src/controllers/orders/order.py:1: IMP002 (warning) `OrderService` is re-exported by `src.services`; import it from there: `from src.services import OrderService`.
src/services/__init__.py:1: IMP001 (warning) `src.services.orders` is a module of this package; import it relatively in __init__.py: `from .orders import ...`.
2 layer finding(s): 0 error(s), 2 warning(s).
```

!!! warning "Quando o pacote fecharia ciclo"
    Às vezes o import pelo pacote da camada fecha um ciclo: no transport,
    task e script importavam `api.dependencies.resources` direto, porque
    importar o pacote `api.dependencies` fecharia ciclo com `src.tasks`. Esse é o caso da exceção
    declarada (`"IMP002 <caminho>::<nome>": "<motivo>"`), não de reescrever o
    import.

#### Router: um por arquivo, `/api` uma vez (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `RTR003` | `routers/` | mais de um `APIRouter(...)` no módulo — um achado por router depois do primeiro |
| `RTR004` | todo o código | `prefix` igual a `/api` (ou abaixo dele) em `APIRouter(...)` ou `include_router(...)` fora de uma função `build_api_router` |

No transport, `/api` estava repetido em 22 `include_router`. Um router de
pedidos com dois routers e o `/api` embutido sai assim (saída real):

```python
# src/api/routers/orders.py
from fastapi import APIRouter

router: APIRouter = APIRouter(prefix="/api/orders", tags=["orders"])
admin_router: APIRouter = APIRouter(prefix="/api/admin/orders", tags=["admin"])
```

```text
src/api/routers/orders.py:3: RTR004 (warning) prefix `/api/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
src/api/routers/orders.py:4: RTR003 (warning) a second APIRouter in this module (`admin_router`); one router per file in routers/ — move it to its own module.
src/api/routers/orders.py:4: RTR004 (warning) prefix `/api/admin/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
3 layer finding(s): 0 error(s), 3 warning(s).
```

A forma que as duas regras pedem: cada domínio num arquivo, só com o próprio
prefixo (`/orders`, `/admin/orders`), e o `/api` posto uma vez:

```python
# src/api/routers/api.py
from fastapi import APIRouter

from src.api.routers.admin_orders import router as admin_orders_router
from src.api.routers.orders import router as orders_router


def build_api_router() -> APIRouter:
    """Monta o router da API: todo domínio fica sob ``/api``."""
    api: APIRouter = APIRouter(prefix="/api")
    api.include_router(orders_router)
    api.include_router(admin_orders_router)
    return api
```

#### A forma da suíte (aviso)

As quatro regras `TST` leem os arquivos que o pytest coleta no `tests/` do
projeto (`test_*.py` e `*_test.py`):

| Código | O que acusa |
| --- | --- |
| `TST001` | teste (`test*` de módulo ou método `test*` de classe `Test*`) cuja docstring não traz `Cenário:` **e** `Esperado:` — a situação que o teste monta e o que ele prova |
| `TST002` | teste que não confere nada: sem `assert`, sem `with pytest.raises` / `warns` / `deprecated_call`, sem chamada `assert*` (`mock.assert_awaited_once()`) |
| `TST003` | `pytest.raises(Exception)` ou `BaseException`, sozinho ou em tupla — qualquer bug passa |
| `TST004` | `parametrize` cujo id leva um valor sorteado no import |

Este arquivo tem as quatro (saída real):

```python
# tests/test_orders.py
from uuid import uuid4

import pytest


@pytest.mark.parametrize("order_id", [str(uuid4())])
def test_order_is_created(order_id: str) -> None:
    """The order is created."""
    with pytest.raises(Exception):
        int(order_id)


def test_receipt_is_sent() -> None:
    """Cenário: um pedido pago.

    Esperado: o recibo sai sem erro.
    """
    str(uuid4())
```

```text
tests/test_orders.py:6: TST004 (warning) parametrize puts a `uuid4()` drawn at import into the test id; each pytest-xdist worker collects different ids and the run aborts — use fixed literals.
tests/test_orders.py:7: TST001 (warning) `test_order_is_created` docstring lacks Cenário:, Esperado:; say the situation the test builds (Cenário:) and what it proves (Esperado:).
tests/test_orders.py:9: TST003 (warning) pytest.raises(Exception) passes for any bug; name the exception the code raises (and `match=` its message when it matters).
tests/test_orders.py:13: TST002 (warning) `test_receipt_is_sent` checks nothing (no assert, no pytest.raises, no assert_* call); a test that cannot fail proves nothing.
4 layer finding(s): 0 error(s), 4 warning(s).
```

!!! info "Quando o valor sorteado quebra o `pytest -n`"
    Cada worker do pytest-xdist importa o módulo por conta própria. Se o id
    do teste depende de um valor sorteado, cada worker coleta ids
    diferentes e a execução aborta com "Different tests were collected".
    Medido com `pytest -n 2`: `random.randint()`, `time.time()`,
    `secrets.token_hex()`, `os.urandom()`, `str(uuid4())`, `uuid4().hex`,
    `f"{uuid4()}"` e `pytest.param(..., id=str(uuid4()))` abortam. Um
    `uuid4()` ou `datetime.now()` **cru** passa — o pytest dá a esses
    objetos o id pela posição (`v0`, `v1`) —, e por isso o `TST004` só os
    acusa quando viram texto ou entram em `ids=` / `id=`.

#### Aviso, erro e exceção declarada

Toda regra tem uma severidade. **Erro** derruba o passo (exit code `1`);
**aviso** sai com `(warning)` depois do código e não derruba. Regra nova
entra como aviso numa release e vira erro na seguinte — assim um serviço
existente tem uma release inteira para migrar. Para migrar uma regra por
vez, selecione-a com `--rule` (repetível, aceita minúsculas):

```bash
tempest lint-layers --rule SVC002              # só uma regra
tempest lint-layers --rule SVC002 --rule RTR001
tempest lint-layers --strict                   # aviso também derruba
```

O `tempest check` roda o passo sem `--rule`. Para o gate derrubar em aviso
também, ligue no `pyproject.toml` — o `--strict`/`--no-strict` da linha de
comando vence a config:

```toml
[tool.tempest]
layers_strict = true
```

**Exceção que o projeto assume fica num lugar só**, com motivo, revisada no
diff como código: o dict `ARCHITECTURE_EXCEPTIONS` em
`tests/architecture_exceptions.py`. A chave é `"<CÓDIGO> <caminho>"` (todas
as violações daquele código no arquivo) ou `"<CÓDIGO> <caminho>::<alvo>"`
(só a que fala daquele nome — a classe, `Classe.método` ou a função). O
caminho é relativo à raiz do projeto. O arquivo também é lido com `ast`,
nunca importado:

```python
"""Exceções de arquitetura que o projeto assume, cada uma com motivo."""

ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "SVC001 src/services/catalogue/catalogue.py::CatalogueService": (
        "catálogo estático, sem repository para o BaseService"
    ),
    "REPO001 src/db/repositories/legacy/legacy.py": "wrapper mantido até a 2.0",
}
```

Com esse arquivo, um `src/services/catalogue/catalogue.py` que define
`CatalogueService` (sem `BaseService`) e uma função de módulo
`load_items`, e nenhum `legacy.py`, o `tempest lint-layers` sai assim
(saída real, exit code `1` por causa do `SVC002`):

```text
src/services/catalogue/catalogue.py:5: SVC002 module-level function `load_items` in services/; move it to utils/, or make it a method of the service that uses it.
tests/architecture_exceptions.py:7: EXC002 (warning) `REPO001 src/db/repositories/legacy/legacy.py` matches no finding; the code it excused is gone or moved — delete the entry, or fix its path.
2 layer finding(s): 1 error(s), 1 warning(s).
```

O `SVC001` do `CatalogueService` sumiu, porque a exceção o cobre. A entrada
de `REPO001` não casa com nada e vira o aviso `EXC002`: exceção não
sobrevive ao código que ela desculpava.

| Código | Severidade | O que acusa |
| --- | --- | --- |
| `EXC001` | erro, sempre (mesmo com `--rule`) | entrada que o checker não consegue usar: chave fora da forma, código desconhecido, motivo vazio ou que não é string literal, chave repetida, arquivo sem o dict |
| `EXC002` | aviso | entrada que não desculpa nenhuma violação — o código mudou ou o caminho está errado |

`--rule` com código desconhecido sai com exit code `2` e a lista dos
códigos válidos. O `tempest check` aceita as mesmas duas opções e as entrega
ao passo `lint-layers` — `tempest check --rule STR002 --strict` roda o gate
inteiro com o passo filtrado e o aviso derrubando (exit code `1` com um
`STR002`, `0` sem `--strict`; medido num projeto do `tempest new`).

!!! tip "Marcador ou exceção declarada?"
    Os marcadores `# tempest: no-base-service` e
    `# tempest: router-builds` continuam valendo. A exceção declarada serve
    para qualquer regra, inclusive as que não têm marcador, e junta num
    arquivo só o que antes ficava espalhado — a review lê o motivo de
    todas de uma vez.

### Metadado de schema — `tempest lint-schemas`

O cliente lê o `openapi.json`, e um campo sem `title`, `description` e
`examples` chega lá só com nome e tipo. Essa regra não sai de `ast`: o
metadado só existe depois que o Pydantic montou o modelo. Por isso ela
**importa** o pacote de schemas (`src.schemas`, ou `app.schemas`), em vez de
ler o código como o `lint-layers`:

```python
# src/schemas/order.py
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class AddressSchema(BaseSchema):
    """Endereço de entrega."""

    city: str = Field(title="Cidade", description="Cidade da entrega.")


class OrderCreateSchema(BaseSchema):
    """Pedido novo."""

    total_cents: int
    address: AddressSchema = Field(title="Endereço", description="Onde entregar.")
```

`tempest lint-schemas` (com `src/schemas/__init__.py` exportando os dois;
saída real, exit code `1`):

```text
src.schemas.order.AddressSchema.city: missing examples
src.schemas.order.OrderCreateSchema.total_cents: missing title, description, examples
2 schema field(s) without documentation.
```

- O campo **folha** (um valor: `str`, `int`, enum, `list[str]`) precisa dos
  três. O campo que **guarda outro schema** (`AddressSchema`,
  `list[ItemSchema]`, `ItemSchema | None`) precisa só de `title` e
  `description` — os exemplos são os do schema de dentro. Por isso o
  `address` passa.
- Cada campo sai uma vez, na classe que o declara, mesmo herdado por vários
  schemas exportados. Os campos que o `BaseResponseSchema` traz (`id`,
  `created_at`...) já vêm documentados.
- Título em branco e `examples=[]` contam como ausentes. Pacote que não
  importa sai com exit code `2` e o traceback.

O mesmo check roda **dentro da suíte**, que é como o projeto do
`tempest new` o cobra a cada `tempest check` — o scaffold já traz este
teste:

```python
# tests/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: os schemas que ``src.schemas`` exporta.

    Esperado: todo campo traz ``title``, ``description`` e ``examples``.
    """
    assert_schema_metadata(src.schemas)
```

---

## Recap

O `tempest` cobre o ciclo inteiro — do scaffold (`new`),
passando pela infra (`generate --docker` / `--dockerfile` / `--src`),
migrações (`db`), usuários (`user`) e segredos (`secrets`), até os gates
de qualidade (`lint` / `fix` / `type` / `test` / `check`), com as regras de camada do SDK (`lint-layers`) dentro do `check`.

## Próximos passos

Depois de gerar o serviço, siga para as receitas relacionadas:

- [Banco de dados »](database.md) — `BaseRepository`, sessões async e o
  fluxo de migrações por trás do `tempest db`.
- [Filas e tarefas »](queue-tasks.md) — as camadas que `tempest generate
  --src` gera para os extras `[queue]` (FastStream) e `[tasks]` (TaskIQ).
- [Deploy seguro »](deploy-safety.md) — migrações destrutivas e shutdown
  gracioso ao containerizar com o `Dockerfile` gerado.
