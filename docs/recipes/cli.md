# CLI


Instalar o `tempest-fastapi-sdk` expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

#### Gerar um novo serviço

```bash
tempest new my_service                          # gera em ./my_service
tempest new my_service --path ~/projects        # diretório-pai customizado
tempest new my_service \
    --bind-host 0.0.0.0 \                       # HOST padrão no .env.example
    --bind-port 9090 \                          # PORT padrão no .env.example
    --extras auth,upload                        # extras do SDK fixados
tempest new my_service --force                  # sobrescreve diretório existente
```

O esqueleto casa com a arquitetura em camadas documentada neste README:

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # fixa tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
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

### `docker-compose.yaml` baseado nos extras

Desde a v0.25.0 o scaffold gera um `docker-compose.yaml` com **apenas** os serviços que os extras escolhidos precisam — sem ZooKeeper, Kafka ou qualquer outra coisa que você não vai usar.

| Extra | Container subido | Porta(s) exposta(s) |
|-------|------------------|---------------------|
| (sempre) | `postgres:16-alpine` | 5432 |
| `[cache]` | `redis:7-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:3-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Exemplo — projeto que usa cache + uploads em S3 + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Gera:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` que cria o bucket `uploads`), `mailhog`
- `.env.example` com `REDIS_URL`, `MINIO_*`, `EMAIL_HOST=localhost`, `EMAIL_PORT=1025`

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
```

### Usuários — `tempest user`

Insere/lista usuários direto no banco usando o `UserModel` concreto do projeto (default `src.db.models:UserModel`). Útil pra bootstrapear o primeiro admin sem rodar SQL manual.

```bash
# Cria usuário comum
tempest user create --email ana@example.com --password senha-forte-12

# Cria admin (pode logar no /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Pede senha interativamente (não fica no shell history)
tempest user create --email admin@local --admin

# Modelo customizado fora do layout scaffoldado — DEVE ser subclasse de BaseUserModel
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Lista
tempest user list                                # todos
tempest user list --admin                        # só admins
```

Resolução do `DATABASE_URL` igual ao `tempest db` (env var > settings > alembic.ini).

#### Gates de qualidade

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
