# Instalação

## Resumo

```bash
pip install tempest-fastapi-sdk
```

Requer **Python 3.11+**.

!!! tip "Use o `uv`"
    `uv add tempest-fastapi-sdk` é mais rápido e já escreve no `pyproject.toml` para você.

!!! info "Primeira vez com Python moderno?"
    Esta página assume que você já tem um ambiente pronto. Se não tem, siga a trilha para iniciantes, que começa do zero absoluto: **[Instalar o uv »](getting-started/uv.md)** → **[Escolher a versão do Python »](getting-started/python-versions.md)** → **[Seu primeiro projeto »](getting-started/first-project.md)** → **[Documentação oficial de referência »](getting-started/references.md)**.

## Extras opcionais

Os helpers mais ricos puxam dependências de terceiros que só são necessárias quando você de fato usa o helper. Escolha os extras que o seu serviço consome:

| Extra | Puxa | Habilita |
| --- | --- | --- |
| `[admin]` | `jinja2`, `itsdangerous` | `AdminSite`, `AdminModel`, `make_admin_router` |
| `[admin-sql]` | `sqlglot` | console SQL do admin: `SqlShellService` + `SqlShellPolicy` (capacidades, tabelas permitidas/negadas, teto de linhas), análise real do statement, auditoria de toda tentativa |
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils`, fluxo bundled `UserAuthService` + `make_auth_router` |
| `[cache]` | `redis` | `AsyncRedisManager` + `@cached` + `RedisIdempotencyStore` |
| `[genai-onnx]` | `onnxruntime`, `tokenizers` | inferência de modelo local exportado para ONNX, sem PyTorch no runtime |
| `[genai-structured]` | `lm-format-enforcer` | saída estruturada garantida por gramática: o modelo local só consegue emitir JSON que casa com o schema |
| `[genai-vlm]` | `pillow`, `torchvision` | modelo de visão-linguagem local: descrever imagem, responder pergunta sobre imagem |
| `[openapi]` | `pyyaml` | ler spec OpenAPI em YAML para `tempest openapi-client` / `tempest openapi-errors` |
| `[pdf-read]` | `pypdf` | **ler** PDF (extrair texto/páginas); o `[pdf]` é para **gerar** |
| `[spreadsheet]` | `openpyxl` | planilhas `.xlsx`: leitura tipada, escrita e import em massa |
| `[websocket]` | `websockets` | Driver de protocolo do `make_websocket_router` — sem ele o handshake devolve 404 |
| `[email]` | `aiosmtplib`, `jinja2`, `email-validator` | `EmailUtils` (com `render_template` + templates Jinja2) |
| `[faces]` | `onnxruntime`, `pillow`, `numpy` | reconhecimento facial em ONNX Runtime, sem opencv e sem torch: `FaceRecognizer` (detectar/embutir/comparar), `compare_faces`. Modelos de 16 MB baixados por `ensure_models()`. **Nenhuma biblioteca de sistema** |
| `[firebase]` | `firebase-admin` | verificação de ID token do Firebase: `FirebaseAuth` (init idempotente, `get_identity` / `get_uid` / `get_optional_identity`), `FirebaseIdentity`, `FirebaseUserResolver`, `FirebaseSettings`. Pesado — 33 pacotes, 52 MB medidos com `firebase-admin` 7.5.0 — e por isso **fora do `[all]`** |
| `[genai]` | `transformers`, `torch`, `accelerate`, `safetensors`, `huggingface-hub` | GenAI local (pesado): `TextGenerator`, `Embedder`, `AIChatPipeline`, `make_genai_router` via HuggingFace/torch |
| `[genai-audio]` | `faster-whisper`, `coqui-tts`, `torch`, `torchaudio`, `torchcodec`, `transformers<5` | STT (Whisper) + TTS (Coqui) — o runtime do Coqui vem junto desde a v0.252.0 |
| `[genai-chroma]` | `chromadb` | vector store Chroma pro RAG |
| `[genai-diarization]` | `sherpa-onnx` | diarização (quem falou quando) via `sherpa-onnx` em ONNX Runtime, sem PyTorch: `SpeakerDiarizer`, `ConversationTranscriber`. Modelos (46 MB) baixados por `ensure_models()` |
| `[genai-hub]` | `huggingface-hub` | ciclo de vida do peso: `resolve_revision` (fixar sha), `download_model` (baixar antes de servir, com preflight de disco), `list_cached_models`/`remove_cached_model`, `tempest model pull`/`cache-list`/`cache-rm` |
| `[genai-image]` | `diffusers`, `pillow` | geração de imagem local: `ImageGenerator` (`generate` texto→imagem, `edit` imagem→imagem), `ImageGenerationConfig`, rota `POST /image` |
| `[genai-ollama]` | `httpx` | backend Ollama: `OllamaGenerator`, `OllamaEmbedder` |
| `[genai-quant]` | `bitsandbytes` | quantização 4/8-bit dos modelos locais do `[genai]` |
| `[genai-rag]` | `trafilatura`, `pymupdf`, `pgvector`, `httpx` | ingestão RAG: scraping web, extração de PDF e embeddings em pgvector |
| `[geo]` | `httpx` | helpers geoespaciais: `haversine_km`, `estimate_travel`, `NominatimBackend`/`OSRMBackend` (geocoding + rotas), `GeoPointMixin` |
| `[http]` | `httpx` | `HTTPClient` + `RetryPolicy` + circuit-breaker |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[mfa]` | `pyotp` | `TOTPHelper` + endpoints MFA/2FA (TOTP) do fluxo bundled de auth |
| `[minio]` | `minio` | `AsyncMinIOClient`, `MinIOUploadStorage` |
| `[modelops]` | `psutil`, `nvidia-ml-py` | benchmark de qualquer callable: latência, RAM, GPU e energia (`benchmark`, `NvmlPowerSampler`, `RaplEnergySampler`) |
| `[modelops-onnx]` | `onnx`, `onnxruntime` | análise estática, benchmark ONNX, `.onnx` → `.ort`, otimização de grafo e quantização — de grafos crus (`analyze_onnx`, `benchmark_onnx`, `export_onnx_to_ort`, `quantize_onnx_dynamic`) e de exports transformers (`optimize_hf_onnx`, `quantize_hf_onnx`) |
| `[modelops-sklearn]` | `skl2onnx` | exportar modelos scikit-learn para ONNX (borda): `export_sklearn_to_onnx`, `verify_sklearn_onnx`, `edge_bundle` |
| `[otel]` | `opentelemetry-sdk`, exporter OTLP + instrumentações FastAPI/SQLAlchemy/httpx | instrumentação OpenTelemetry via `setup_tracing` |
| `[pdf]` | `weasyprint`, `jinja2` | geração de PDF a partir de templates HTML: `PdfRenderer`, cinco documentos prontos com schema tipado, `make_pdf_router`, `tempest pdf`. **Exige Pango + fontconfig no sistema** — veja a receita |
| `[postgres]` | `asyncpg` | driver async PostgreSQL para URLs `postgresql+asyncpg://` (produção) |
| `[prometheus]` | `prometheus-client` | `PrometheusMiddleware`, `make_prometheus_router`, `make_prometheus_registry` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` |
| `[sqlite]` | `aiosqlite` | driver async SQLite para URLs `sqlite+aiosqlite://` (default de dev) |
| `[ssr]` | `tempestweb` | SSR com HTMX: `build_web_app`, `make_htmx_router`, `Page`, helper `htmx` |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `[tasks-redis]` | `taskiq`, `taskiq-redis` | `TaskQueue.redis` / `TaskQueue.from_settings` sobre Redis Streams, result backend e o lease do scheduler |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `DownloadUtils`, `LocalUploadStorage` |
| `[vision]` | `ort-vision-sdk` | helpers de visão (`Detector`, `Classifier`, `Segmenter` + `to_detection_schemas`/`to_classification_schema`/`to_segmentation_schemas`) |
| `[webauthn]` | `fido2` | passkeys / chaves de segurança: `WebAuthnService`, `make_web_authn_credential_model`, rotas `/auth/webauthn/*` — login sem senha, resistente a phishing |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[all]` | tudo acima **exceto** os 15 extras de stack pesado ou binário nativo: `[genai]`, `[genai-audio]`, `[genai-diarization]`, `[genai-hub]`, `[genai-image]`, `[genai-onnx]`, `[genai-quant]`, `[genai-rag]`, `[genai-structured]`, `[genai-vlm]`, `[faces]`, `[modelops-onnx]`, `[modelops-sklearn]`, `[admin-sql]`, `[firebase]` | os helpers de aplicação — instale os 15 acima à parte |

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
    uv add "tempest-fastapi-sdk[auth,upload,postgres]>=0.171.0"
    ```

=== "pyproject.toml"

    ```toml
    dependencies = [
        "tempest-fastapi-sdk[auth,upload,postgres]>=0.171.0",
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
| 3.10 e anteriores | Não suportado (usa a sintaxe ``X | None`` do PEP 604) |
