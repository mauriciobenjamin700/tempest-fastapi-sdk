# Docker: compose e Dockerfile

Use para gerar o `docker-compose.yaml` a partir dos extras, regerá-lo num projeto existente e containerizar a app.

Parte da receita [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli.md).

## `docker-compose.yaml` baseado nos extras

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

### Profile `prod`: a API dentro do compose

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

#### Migrations no profile `prod`

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

## Regerar o `docker-compose.yaml` num projeto existente

Quando você muda os extras instalados (`uv add "tempest-fastapi-sdk[minio]"`) ou o SDK bumpa as imagens, regenere com:

```bash
tempest generate --docker                        # lê extras do pyproject.toml local
tempest generate --docker --extras cache,minio   # força extras explicitos
tempest generate --docker --name my-svc          # sobrescreve prefixo do container
tempest generate --docker --force                # sobrescreve compose existente
```

O comando lê o `[project] name` + extras do `pyproject.toml` do diretório atual (use `--path` pra outro). Recusa overwrite sem `--force` pra não pisar em edits manuais. O `.env.example` é atualizado de forma idempotente — re-rodar não duplica blocos.

## Dockerfile para containerizar a app

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

    Sirva o resultado com [`make_spa_router("web/dist")`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/react-spa.md),
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

### Regerar o Dockerfile — `tempest generate --dockerfile`

```bash
tempest generate --dockerfile                    # Dockerfile + .dockerignore
tempest generate --dockerfile --name my-svc      # sobrescreve nome nos comentários
tempest generate --dockerfile --force            # sobrescreve arquivos existentes
tempest generate --docker --dockerfile --src     # tudo numa tacada
```

A porta do `EXPOSE` / `SERVER_PORT` é lida do `SERVER_PORT` no `.env` (ou `.env.example`), caindo em `8000` se não achar. Como os outros generators, recusa overwrite sem `--force`.
