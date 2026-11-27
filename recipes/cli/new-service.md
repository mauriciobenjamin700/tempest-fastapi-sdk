# Instalar e gerar um serviço

Use para escolher entre `[cli-ops]` e `[cli]`, gerar um serviço novo e trazer um projeto existente ao padrão (`tempest new`, `tempest generate`).

Parte da receita [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli.md).

## Servidor x desenvolvimento: `[cli-ops]` e `[cli]`

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

## Gerar um novo serviço

```bash
tempest new my_service                          # gera em ./my_service
tempest new my_service --path ~/projects        # diretório-pai customizado
# HOST/PORT padrão no .env.example e extras do SDK fixados no pyproject.toml
tempest new my_service --bind-host 0.0.0.0 --bind-port 9090 --extras upload
tempest new my_service --force                  # sobrescreve diretório existente
```

O esqueleto casa com a arquitetura em camadas documentada em [Arquitetura »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/architecture.md):

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
│   │   ├── docs/            # base.py (EndpointDoc) + service_info.py — texto do OpenAPI fora do router
│   │   └── dependencies/    # auth.py + resources.py (db, get_session, get_mailer/get_storage por extra) + controllers.py (providers)
│   ├── controllers/system/  # ServiceInfoController — um pacote por contexto, uma classe por módulo
│   ├── services/            # lógica de negócio, em services/<contexto>/
│   ├── schemas/             # DTOs Pydantic
│   ├── scripts/             # composition.py — o composition root (build_*)
│   ├── core/
│   │   ├── settings.py
│   │   ├── constants/       # constantes por domínio + messages/{errors,successes}.py
│   │   ├── enums/           # um módulo por domínio
│   │   ├── types/           # Protocol, alias, NamedTuple — um módulo por domínio
│   │   └── exceptions/      # um módulo por status: bad_request, unauthorized, forbidden, not_found, conflict
│   ├── db/
│   │   ├── configs/         # names.py + connection.py (make_db_manager) + migrations.py (run_migrations)
│   │   ├── models/users/    # UserModel — só com [auth] (ou [admin])
│   │   └── repositories/
│   ├── queue/               # só com o extra [queue] — broker + handlers
│   ├── tasks/               # só com [tasks] ou [tasks-redis] — queue, names, dispatch, worker + uma task de exemplo
│   ├── ui/                  # só com o extra [ssr] — pages/layout/components/styles
│   └── utils/
└── tests/
    ├── conftest.py          # um SQLite por worker do pytest-xdist
    ├── integration/system/  # test_service_info.py: /api/, override de settings, /health/liveness
    └── unit/
        ├── architecture/    # lint_layers, analyze_paths, assert_schema_metadata
        └── core/            # test_exceptions_contract.py: status, code, mensagem e field congelados
```

O porquê de cada pasta, e o que cobra cada uma, está em
[Padrão de serviço](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/service-standard.md).

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
    [`read_project_version`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/settings.md#tempest_fastapi_sdk.settings.version.read_project_version).
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

## Gerar as camadas `src` dos extras — `tempest generate --src`

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
| `[queue]` | `queue/broker.py` (`RabbitBroker` + `AsyncQueueManager` + `get_broker`), `queue/__init__.py` (só reexporta), `queue/handlers.py` (subscriber de exemplo) |
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

## Trazer um projeto antigo ao padrão — `tempest generate --standard`

Escreve as peças do [padrão de serviço](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/service-standard.md) que um projeto anterior a ele não tem — `core/constants/`, `core/enums/`, `core/types/`, os guards de `tests/unit/` —, converte um `core/exceptions.py` plano no pacote `core/exceptions/` com um módulo por status e congela o contrato das exceções em `tests/unit/core/test_exceptions_contract.py`. Arquivo existente é pulado sem `--force`; a migração recusa, sem escrever nada, quando o módulo tem algo além de import, classe e `__all__`. O passo a passo está em [Projeto existente](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/service-standard.md#projeto-existente-tempest-generate-standard).

```bash
tempest generate --standard
```
