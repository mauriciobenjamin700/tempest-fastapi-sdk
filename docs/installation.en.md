# Installation

## TL;DR

```bash
pip install tempest-fastapi-sdk
```

Requires **Python 3.11+**.

!!! tip "Use `uv` instead"
    `uv add tempest-fastapi-sdk` is faster and writes to `pyproject.toml` for you.

## Optional extras

Feature-rich helpers pull in third-party dependencies that you only need when you actually use the helper. Pick the extras your service consumes:

| Extra | Pulls in | Unlocks |
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
| `[all]` | everything above | every helper |

=== "Subset (recommended)"

    ```bash
    pip install "tempest-fastapi-sdk[auth,upload,cache]"
    ```

=== "Everything"

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

!!! info "Lazy imports"
    Since 0.7.1 every optional dependency is imported lazily at first instantiation, so `import tempest_fastapi_sdk` works even when only a subset of extras is installed. Instantiating a helper whose extra is missing raises `ImportError` with a clear hint pointing at the right extra.

## CLI

The `tempest` CLI ships in the base install (no extra needed):

```bash
tempest --version              # show installed SDK version
tempest new                    # scaffold a layered service in cwd
tempest new myproject          # scaffold inside ./myproject
tempest fix                    # ruff check --fix . + ruff format .
tempest check                  # lint + fmt-check + mypy + pytest
```

See **[Recipes → CLI »](recipes/cli.md)** for the full breakdown.

## Verify the install

```bash
python -c "import tempest_fastapi_sdk; print(tempest_fastapi_sdk.__version__)"
```

## Python version policy

| Python | Status |
| --- | --- |
| 3.13 | Primary CI matrix |
| 3.12 | Supported |
| 3.11 | Supported (minimum) |
| 3.10 and older | Not supported (uses `X \| None` PEP 604 syntax) |
