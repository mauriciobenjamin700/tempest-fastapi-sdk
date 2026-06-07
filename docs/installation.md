# Instalação

## Resumo

```bash
pip install tempest-fastapi-sdk
```

Requer **Python 3.11+**.

!!! tip "Use o `uv`"
    `uv add tempest-fastapi-sdk` é mais rápido e já escreve no `pyproject.toml` para você.

## Extras opcionais

Os helpers mais ricos puxam dependências de terceiros que só são necessárias quando você de fato usa o helper. Escolha os extras que o seu serviço consome:

| Extra | Puxa | Habilita |
| --- | --- | --- |
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils`, fluxo bundled `UserAuthService` + `make_auth_router` |
| `[email]` | `aiosmtplib`, `jinja2`, `email-validator` | `EmailUtils` (com `render_template` + templates Jinja2) |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `DownloadUtils`, `LocalUploadStorage` |
| `[cache]` | `redis` | `AsyncRedisManager` + `@cached` + `RedisIdempotencyStore` |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `[admin]` | `jinja2`, `itsdangerous` | `AdminSite`, `AdminModel`, `make_admin_router` |
| `[minio]` | `minio` | `AsyncMinIOClient`, `MinIOUploadStorage` |
| `[http]` | `httpx` | `HTTPClient` + `RetryPolicy` + circuit-breaker |
| `[prometheus]` | `prometheus-client` | `PrometheusMiddleware`, `make_prometheus_router`, `make_prometheus_registry` |
| `[mfa]` | `pyotp` | `TOTPHelper` + endpoints MFA/2FA (TOTP) do fluxo bundled de auth |
| `[sqlite]` | `aiosqlite` | driver async SQLite para URLs `sqlite+aiosqlite://` (default de dev) |
| `[postgres]` | `asyncpg` | driver async PostgreSQL para URLs `postgresql+asyncpg://` (produção) |
| `[all]` | tudo acima | todos os helpers |

=== "Subconjunto (recomendado)"

    ```bash
    pip install "tempest-fastapi-sdk[auth,upload,cache]"
    ```

=== "Tudo"

    ```bash
    pip install "tempest-fastapi-sdk[all]"
    ```

=== "uv add"

    ```bash
    uv add "tempest-fastapi-sdk[auth,upload,postgres]>=0.37.0"
    ```

=== "pyproject.toml"

    ```toml
    dependencies = [
        "tempest-fastapi-sdk[auth,upload,postgres]>=0.37.0",
    ]
    ```

!!! warning "O SDK não traz driver de banco por padrão"
    `sqlalchemy[asyncio]` é dependência core, mas o DBAPI async é escolha
    do seu deploy: instale `[sqlite]` (`aiosqlite`, default de dev) ou
    `[postgres]` (`asyncpg`, produção). Sem nenhum, o engine levanta
    `ModuleNotFoundError` do driver na primeira conexão. Serviços
    criados com `tempest new` já pinam `aiosqlite` e carregam uma linha
    `asyncpg` comentada no `pyproject.toml`.

!!! info "Imports preguiçosos"
    Desde a 0.7.1 toda dependência opcional é importada de forma preguiçosa na primeira instanciação, então `import tempest_fastapi_sdk` funciona mesmo quando só um subconjunto de extras está instalado. Instanciar um helper cujo extra está faltando levanta `ImportError` com uma dica clara apontando para o extra certo.

## CLI

A CLI `tempest` vem na instalação base (sem extra):

```bash
tempest --version              # mostra a versão instalada do SDK
tempest new                    # gera um serviço em camadas no diretório atual
tempest new myproject          # gera dentro de ./myproject
tempest generate --docker      # regenera docker-compose.yaml a partir dos extras já escolhidos
tempest db init                # bootstrapa diretório alembic (alembic.ini sem credenciais)
tempest db revision -m "msg"   # autogenerate revision aplicando o reorder hook
tempest db upgrade             # roda upgrade até head (lê DATABASE_URL do .env)
tempest db downgrade -1        # volta uma revisão
tempest db current             # mostra revisão atual
tempest db history             # log de revisões
tempest user create --email admin@local --admin   # `--email` obrigatório; senha pedida interativamente
tempest user list --admin      # lista somente os admins (omita `--admin` pra listar todos)
tempest fix                    # ruff check --fix . + ruff format .
tempest check                  # lint + fmt-check + mypy + pytest
```

Veja **[Receitas → CLI »](recipes/cli.md)** para o detalhamento completo.

## Verifique a instalação

```bash
python -c "import tempest_fastapi_sdk; print(tempest_fastapi_sdk.__version__)"
```

## Política de versões do Python

| Python | Status |
| --- | --- |
| 3.13 | Matriz principal do CI |
| 3.12 | Suportado |
| 3.11 | Suportado (mínimo) |
| 3.10 e anteriores | Não suportado (usa a sintaxe `X \| None` do PEP 604) |
