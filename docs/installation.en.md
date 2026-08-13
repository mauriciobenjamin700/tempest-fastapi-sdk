# Installation

## TL;DR

```bash
pip install tempest-fastapi-sdk
```

Requires **Python 3.11+**.

!!! tip "Use `uv` instead"
    `uv add tempest-fastapi-sdk` is faster and writes to `pyproject.toml` for you.

!!! info "First time with modern Python?"
    This page assumes you already have a working environment. If you do not, follow the beginner track, which starts from absolute zero: **[Install uv »](getting-started/uv.md)** → **[Pick your Python version »](getting-started/python-versions.md)** → **[Your first project »](getting-started/first-project.md)** → **[Official reference docs »](getting-started/references.md)**.

## Optional extras

Feature-rich helpers pull in third-party dependencies that you only need when you actually use the helper. Pick the extras your service consumes:

| Extra | Pulls in | Unlocks |
| --- | --- | --- |
| `[admin]` | `jinja2`, `itsdangerous` | `AdminSite`, `AdminModel`, `make_admin_router` |
| `[admin-sql]` | `sqlglot` | admin SQL console: `SqlShellService` + `SqlShellPolicy` (capabilities, allowed/denied tables, row cap), real statement parsing, every attempt audited |
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils`, bundled `UserAuthService` + `make_auth_router` flow |
| `[cache]` | `redis` | `AsyncRedisManager` + `@cached` + `RedisIdempotencyStore` |
| `[websocket]` | `websockets` | Protocol driver for `make_websocket_router` — without it the handshake 404s |
| `[email]` | `aiosmtplib`, `jinja2`, `email-validator` | `EmailUtils` (with `render_template` + Jinja2 templates) |
| `[genai]` | `transformers`, `torch`, `accelerate`, `safetensors`, `huggingface-hub` | local (heavy) GenAI: `TextGenerator`, `Embedder`, `AIChatPipeline`, `make_genai_router` via HuggingFace/torch |
| `[genai-audio]` | `faster-whisper`, `coqui-tts` | STT (Whisper) + TTS (Coqui) |
| `[genai-chroma]` | `chromadb` | Chroma vector store for RAG |
| `[genai-hub]` | `huggingface-hub` | weight lifecycle: `resolve_revision` (pin a sha), `download_model` (fetch before serving, with a disk preflight), `list_cached_models`/`remove_cached_model`, `tempest model pull`/`cache-list`/`cache-rm` |
| `[genai-image]` | `diffusers`, `pillow` | local image generation: `ImageGenerator` (`generate` text→image, `edit` image→image), `ImageGenerationConfig`, `POST /image` route |
| `[genai-ollama]` | `httpx` | Ollama backend: `OllamaGenerator`, `OllamaEmbedder` |
| `[genai-quant]` | `bitsandbytes` | 4/8-bit quantization for the local `[genai]` models |
| `[genai-rag]` | `trafilatura`, `pymupdf`, `pgvector`, `httpx` | RAG ingestion: web scraping, PDF extraction, and pgvector embeddings |
| `[geo]` | `httpx` | geospatial helpers: `haversine_km`, `estimate_travel`, `NominatimBackend`/`OSRMBackend` (geocoding + routing), `GeoPointMixin` |
| `[http]` | `httpx` | `HTTPClient` + `RetryPolicy` + circuit-breaker |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[mfa]` | `pyotp` | `TOTPHelper` + MFA/2FA (TOTP) endpoints on the bundled auth flow |
| `[minio]` | `minio` | `AsyncMinIOClient`, `MinIOUploadStorage` |
| `[modelops]` | `psutil`, `nvidia-ml-py` | benchmark any callable: latency, RAM, GPU and energy (`benchmark`, `NvmlPowerSampler`, `RaplEnergySampler`) |
| `[modelops-onnx]` | `onnx`, `onnxruntime` | static analysis, ONNX benchmarking, `.onnx` → `.ort`, graph optimization and quantization — of raw graphs (`analyze_onnx`, `benchmark_onnx`, `export_onnx_to_ort`, `quantize_onnx_dynamic`) and of transformers exports (`optimize_hf_onnx`, `quantize_hf_onnx`) |
| `[modelops-sklearn]` | `skl2onnx` | export scikit-learn models to ONNX for the edge: `export_sklearn_to_onnx`, `verify_sklearn_onnx`, `edge_bundle` |
| `[otel]` | `opentelemetry-sdk`, OTLP exporter + FastAPI/SQLAlchemy/httpx instrumentations | OpenTelemetry instrumentation via `setup_tracing` |
| `[postgres]` | `asyncpg` | PostgreSQL async driver for `postgresql+asyncpg://` URLs (production) |
| `[prometheus]` | `prometheus-client` | `PrometheusMiddleware`, `make_prometheus_router`, `make_prometheus_registry` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` |
| `[sqlite]` | `aiosqlite` | SQLite async driver for `sqlite+aiosqlite://` URLs (dev default) |
| `[ssr]` | `tempestweb` | HTMX-based SSR: `build_web_app`, `make_htmx_router`, `Page`, the `htmx` helper |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `DownloadUtils`, `LocalUploadStorage` |
| `[vision]` | `ort-vision-sdk` | vision helpers (`Detector`, `Classifier`, `Segmenter` + `to_detection_schemas`/`to_classification_schema`/`to_segmentation_schemas`) |
| `[webauthn]` | `fido2` | Passkeys / security keys: `WebAuthnService`, `make_web_authn_credential_model`, the `/auth/webauthn/*` routes — passwordless, phishing-resistant login |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[all]` | everything above **except** the heavy GenAI stacks (`[genai]`, `[genai-quant]`, `[genai-rag]`, `[genai-audio]`) | every helper except the heavy GenAI ones — install `[genai]`/`[genai-rag]`/etc. separately |

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
    uv add "tempest-fastapi-sdk[auth,upload,postgres]>=0.171.0"
    ```

=== "pyproject.toml"

    ```toml
    dependencies = [
        "tempest-fastapi-sdk[auth,upload,postgres]>=0.171.0",
    ]
    ```

!!! warning "The SDK ships no database driver by default"
    `sqlalchemy[asyncio]` is core, but the async DBAPI is your deploy
    choice: install `[sqlite]` (`aiosqlite`, dev default) or `[postgres]`
    (`asyncpg`, production). Without one, the engine raises
    `ModuleNotFoundError` for the driver on first connection. Services
    scaffolded with `tempest new` already pin `aiosqlite` and carry a
    commented `asyncpg` line in `pyproject.toml`.

!!! info "Lazy imports"
    Since 0.7.1 every optional dependency is imported lazily at first instantiation, so `import tempest_fastapi_sdk` works even when only a subset of extras is installed. Instantiating a helper whose extra is missing raises `ImportError` with a clear hint pointing at the right extra.

## CLI

The `tempest` CLI ships in the base install (no extra needed):

```bash
tempest --version              # show installed SDK version
tempest new                    # scaffold a layered service in cwd
tempest new myproject          # scaffold inside ./myproject
tempest generate --docker      # regenerate docker-compose.yaml from chosen extras
tempest db init                # bootstrap alembic dir (alembic.ini with no credentials)
tempest db revision -m "msg"   # autogenerate revision with the reorder hook applied
tempest db upgrade             # run upgrade to head (reads DATABASE_URL from .env)
tempest db downgrade -1        # roll back one revision
tempest db current             # show current revision
tempest db history             # revision log
tempest user create --email admin@local --admin   # `--email` required; password prompted interactively
tempest user list --admin      # list admins only (drop `--admin` to list everyone)
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
