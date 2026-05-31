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
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils` |
| `[email]` | `aiosmtplib` | `EmailUtils` |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `DownloadUtils` |
| `[cache]` | `redis` | `AsyncRedisManager` + `@cached` |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `[admin]` | `jinja2`, `itsdangerous` | `AdminSite`, `AdminModel`, `make_admin_router` |
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
    uv add "tempest-fastapi-sdk[auth,upload]>=0.19.0"
    ```

=== "pyproject.toml"

    ```toml
    dependencies = [
        "tempest-fastapi-sdk[auth,upload]>=0.19.0",
    ]
    ```

!!! info "Imports preguiçosos"
    Desde a 0.7.1 toda dependência opcional é importada de forma preguiçosa na primeira instanciação, então `import tempest_fastapi_sdk` funciona mesmo quando só um subconjunto de extras está instalado. Instanciar um helper cujo extra está faltando levanta `ImportError` com uma dica clara apontando para o extra certo.

## CLI

A CLI `tempest` vem na instalação base (sem extra):

```bash
tempest --version              # mostra a versão instalada do SDK
tempest new                    # gera um serviço em camadas no diretório atual
tempest new myproject          # gera dentro de ./myproject
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
