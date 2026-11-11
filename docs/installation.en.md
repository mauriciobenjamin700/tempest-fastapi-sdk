# Installation

## TL;DR

```bash
pip install tempest-fastapi-sdk
```

Requires **Python 3.12+** (3.12, 3.13 and 3.14).

!!! tip "Use `uv` instead"
    `uv add tempest-fastapi-sdk` is faster and writes to `pyproject.toml` for you.

!!! info "The `tempest` command is the `[cli]` extra (or `[cli-ops]` on a server)"
    Since 0.308.0 the CLI (`tempest new`, `tempest check`, `tempest db`, …) is not part of the base package — the quality gate brings `ruff`, which has no place in a production image. In development, install it as a tool: `uv tool install "tempest-fastapi-sdk[cli]"`, or in the project's dev group: `uv add --dev "tempest-fastapi-sdk[cli]"`. To have `tempest` on a server (`doctor`, `db`, `shell` and the project's commands), use `[cli-ops]`, which brings neither `tempest-cli` nor `ruff`: `uv add "tempest-fastapi-sdk[cli-ops]"`.

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
| `[cli]` | `tempest-cli` (which requires `ruff`), `typer`, `click` | the whole `tempest` command (`new`, `check`, `db`, …), quality gate included. Dev tooling: `uv add --dev "tempest-fastapi-sdk[cli]"` or `uv tool install "tempest-fastapi-sdk[cli]"`. Without it or `[cli-ops]`, `tempest` prints the install line and exits with code 2. Included in `[all]`. [Recipe](recipes/cli.md) |
| `[cli-ops]` | `typer`, `click` | the `tempest` command without the gate, for a server: `doctor`, `db`, `shell`, `user`, `routes` and `[tool.tempest] commands` run; `tempest lint` and the other gate commands exit 2 naming `[cli]`. No `tempest-cli`, no `ruff`. `[cli]` contains it. [Recipe](recipes/cli.md#server-vs-development-cli-ops-and-cli) |
| `[genai-onnx]` | `onnxruntime`, `tokenizers` | Local-model inference from an ONNX export, with no PyTorch at runtime |
| `[genai-structured]` | `lm-format-enforcer` | Grammar-enforced structured output: the local model can only emit JSON matching the schema |
| `[genai-vlm]` | `pillow`, `torchvision` | Local vision-language model: describe an image, answer a question about one |
| `[openapi]` | `pyyaml` | Read a YAML OpenAPI spec for `tempest openapi-client` / `tempest openapi-errors` |
| `[pdf-read]` | `pypdf` | **Read** PDFs (extract text/pages); `[pdf]` is for **generating** them |
| `[pdf-layout]` | `pdfplumber` | Read PDFs **with layout and tables** (columns recovered, tables as grids); slower than `[pdf-read]` |
| `[spreadsheet]` | `openpyxl` | `.xlsx` spreadsheets: typed reading, writing and bulk import |
| `[websocket]` | `websockets` | Protocol driver for `make_websocket_router` — without it the handshake 404s |
| `[email]` | `aiosmtplib`, `jinja2`, `email-validator` | `EmailUtils` (with `render_template` + Jinja2 templates) |
| `[faces]` | `onnxruntime`, `pillow`, `numpy` | Face recognition on ONNX Runtime, no opencv and no torch: `FaceRecognizer` (detect/embed/compare), `compare_faces`. 16 MB models fetched by `ensure_models()`. **No system libraries** |
| `[firebase]` | `firebase-admin` | Firebase ID token verification: `FirebaseAuth` (idempotent init, `get_identity` / `get_uid` / `get_optional_identity`), `FirebaseIdentity`, `FirebaseUserResolver`, `FirebaseSettings`. Heavy — 33 packages, 52 MB measured on `firebase-admin` 7.5.0 — and therefore **out of `[all]`** |
| `[genai]` | `transformers`, `torch`, `accelerate`, `safetensors`, `huggingface-hub` | local (heavy) GenAI: `TextGenerator`, `Embedder`, `MusicGenerator`, `BarkSpeech`, `AIChatPipeline`, `make_genai_router` via HuggingFace/torch |
| `[genai-audio]` | `faster-whisper`, `torch` | STT (Whisper). Since 0.349.0 it no longer brings Coqui: its `torchaudio` broke Bark on a GPU ([details](#genai-audio-coqui-split)) |
| `[genai-tts-coqui]` | `coqui-tts`, `torch`, `torchaudio`, `torchcodec`, `six` | TTS (Coqui XTTS v2) — the Coqui runtime ships with it; no `transformers<5` cap since 0.342.0 ([details](#genai-audio-transformers-bound)) |
| `[genai-chroma]` | `chromadb` | Chroma vector store for RAG |
| `[genai-diarization]` | `sherpa-onnx` | Speaker diarization (who spoke when) via `sherpa-onnx` on ONNX Runtime, no PyTorch: `SpeakerDiarizer`, `ConversationTranscriber`. Models (46 MB) fetched by `ensure_models()` |
| `[genai-hosted]` | `httpx` (already in the base package) | hosted providers: `AnthropicGenerator`, `OpenAICompatGenerator` (OpenAI, DeepSeek), `OpenAICompatEmbedder`, `OpenAICompatSpeech` (remote transcription), `HostedGenAISettings`. No vendor SDK |
| `[genai-hub]` | `huggingface-hub` | weight lifecycle: `resolve_revision` (pin a sha), `download_model` (fetch before serving, with a disk preflight), `list_cached_models`/`remove_cached_model`, `tempest model pull`/`cache-list`/`cache-rm` |
| `[genai-image]` | `diffusers`, `pillow` | local image generation: `ImageGenerator` (`generate` text→image, `edit` image→image), `ImageGenerationConfig`, `POST /image` route |
| `[genai-sound]` | `diffusers` | local sound effects and music (with `[genai]`): `AudioGenerator`, `AudioGenerationConfig`, `GeneratedAudio`, `POST /audio` route |
| `[genai-ollama]` | `httpx` | Ollama backend: `OllamaGenerator`, `OllamaEmbedder`, `OllamaSpeech` (speech and transcription) |
| `[genai-quant]` | `bitsandbytes` | 4/8-bit quantization for the local `[genai]` models |
| `[genai-rag]` | `trafilatura`, `pymupdf`, `pgvector`, `httpx` | RAG ingestion: web scraping, PDF extraction, and pgvector embeddings |
| `[geo]` | `httpx` | geospatial helpers: `haversine_km`, `estimate_travel`, `NominatimBackend`/`OSRMBackend` (geocoding + routing), `GeoPointMixin` |
| `[http]` | `httpx` (already in the base package) | `HTTPClient` + `RetryPolicy` + circuit-breaker. The extra is optional: `HTTPClient` works on the base install, and declaring the extra only records the intent in the service's `pyproject.toml` |
| `[image]` | `pillow` | `strip_image_metadata`: re-encodes a user's photo in the same format without EXIF (GPS, device), XMP, IPTC, ICC and text chunks, with a pixel ceiling against decompression bombs |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[mfa]` | `pyotp`, `segno` | `TOTPHelper` + MFA/2FA (TOTP) endpoints on the bundled auth flow + enrollment QR code (`provisioning_qr_terminal`, `provisioning_qr_data_uri`) |
| `[minio]` | `minio` | `AsyncMinIOClient`, `MinIOUploadStorage` |
| `[modelops]` | `psutil`, `nvidia-ml-py`, `numpy` | benchmark any callable: latency, RAM, GPU and energy (`benchmark`, `NvmlPowerSampler`, `RaplEnergySampler`); drift monitoring (`baseline_from_samples`, `PredictionMonitor`) and reading the compact format (`read_compact`, `predict_compact`) |
| `[modelops-compose]` | `ort-vision-sdk[compose]` | fuse a detector and a classifier into one `.onnx`: `fuse_detect_classify`. Running the fused graph is `[vision]` (`DetectClassify`), which pulls no `onnx` |
| `[modelops-onnx]` | `onnx`, `onnxruntime` | static analysis, ONNX benchmarking, `.onnx` → `.ort`, graph optimization and quantization — of raw graphs (`analyze_onnx`, `benchmark_onnx`, `export_onnx_to_ort`, `quantize_onnx_dynamic`) and of transformers exports (`optimize_hf_onnx`, `quantize_hf_onnx`) |
| `[modelops-sklearn]` | `skl2onnx` | export scikit-learn models to ONNX for the edge: `export_sklearn_to_onnx`, `verify_sklearn_onnx`, `edge_bundle` |
| `[oidc]` | `PyJWT`, `cryptography` | offline verification of OIDC realm access tokens (Keycloak, Auth0, Okta …): `OIDCTokenVerifier`, and `OIDCProvider(token_verifier=...)` for social login |
| `[otel]` | `opentelemetry-sdk`, OTLP exporter + FastAPI/SQLAlchemy/httpx instrumentations | OpenTelemetry instrumentation via `setup_tracing` |
| `[pdf]` | `weasyprint`, `jinja2`, `segno` | PDF generation from HTML templates: `PdfRenderer`, five bundled typed documents, `make_pdf_router`, `tempest pdf`. **Needs Pango + fontconfig on the system** — see the recipe |
| `[postgres]` | `asyncpg` | PostgreSQL async driver for `postgresql+asyncpg://` URLs (production) |
| `[prometheus]` | `prometheus-client` | `PrometheusMiddleware`, `make_prometheus_router`, `make_prometheus_registry` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` |
| `[slides]` | `weasyprint`, `jinja2`, `python-pptx`, `python-docx`, `markdown` | Slides and answer keys from structured text (HTML, PDF, PPTX, DOCX, Markdown) and Markdown to PDF with your own or the ABNT stylesheet: `parse_slides`, `SlideRenderer`, `AnswerKeyRenderer`, `MarkdownPdfRenderer`. **Needs Pango + fontconfig on the system**, like `[pdf]`. [Recipe](recipes/slides.md) |
| `[slides-mermaid]` | `playwright` | Drawing `` ```mermaid `` blocks in `MarkdownPdfRenderer` (`render_mermaid=True`). Needs `playwright install chromium`; that is why it stays **out of `[all]`** |
| `[sqlite]` | `aiosqlite` | SQLite async driver for `sqlite+aiosqlite://` URLs (dev default) |
| `[ssr]` | `tempestweb` | HTMX-based SSR: `build_web_app`, `make_htmx_router`, `Page`, the `htmx` helper |
| `[storage-encryption]` | `cryptography` | Encryption at rest of `LocalObjectStorageClient` (`encryption_keys`, `STORAGE_LOCAL_ENCRYPTION_KEYS`) and the `tempest storage encrypt-local` / `reencrypt-local` commands. [Recipe](recipes/local-storage.md#encryption-at-rest) |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `[tasks-redis]` | `taskiq`, `taskiq-redis` | `TaskQueue.redis` / `TaskQueue.from_settings` over Redis Streams, the result backend and the scheduler lease |
| `[tests]` | `pytest`, `pytest-asyncio`, `pytest-xdist` | the service's suite and `tempest test` in parallel (the default since `tempest-cli` 0.6.0). No bound of its own; inherits `pytest<10` from `pytest-asyncio` and `pluggy<2` from `pytest`. Test tooling, not a helper — kept **out of `[all]`**. [Recipe](recipes/testing.md#the-suite-in-parallel-the-tempest-test-default) |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `DownloadUtils`, `LocalUploadStorage` |
| `[vision]` | `ort-vision-sdk` | vision helpers (`Detector`, `Classifier`, `Segmenter` + `to_detection_schemas`/`to_classification_schema`/`to_segmentation_schemas`) |
| `[webauthn]` | `fido2` | Passkeys / security keys: `WebAuthnService`, `make_web_authn_credential_model`, the `/auth/webauthn/*` routes — passwordless, phishing-resistant login |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[all]` | everything above **except** the 18 extras that pull a heavy stack or a native binary: `[genai]`, `[genai-audio]`, `[genai-tts-coqui]`, `[genai-diarization]`, `[genai-hub]`, `[genai-image]`, `[genai-onnx]`, `[genai-sound]`, `[genai-quant]`, `[genai-rag]`, `[genai-structured]`, `[genai-vlm]`, `[faces]`, `[modelops-onnx]`, `[modelops-sklearn]`, `[admin-sql]`, `[firebase]`, `[slides-mermaid]` | the application helpers — install the 18 above separately (and `[tests]`, which is test tooling, in the dev group) |

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

<a id="genai-audio-coqui-split"></a>

!!! warning "Since 0.349.0 Coqui (XTTS) lives in `[genai-tts-coqui]`"
    `[genai-audio]` used to bring `coqui-tts` and, with it, `torchaudio`.
    Bark does not use `torchaudio`, but `transformers` imports it when it is
    installed, while building the `AutoProcessor`. On a GPU the PyPI
    `torchaudio` 2.11.1 is a CUDA 13 build that does not load next to a
    CUDA 12 `torch`, so `POST /audio/speech` answered 500 (measured on
    tempest-ai, RTX 4070 Ti SUPER, SDK 0.348.0 — issue #723).

    Now `[genai-audio]` is speech-to-text only (`faster-whisper` + `torch`)
    and Coqui has an extra of its own. Resolved with `uv pip compile`
    (Python 3.12) on 2026-11-10:

    | Extra | brings | does not bring |
    | --- | --- | --- |
    | `[genai-audio]` | faster-whisper 1.2.1, ctranslate2 4.8.5, torch 2.15.0 | `torchaudio`, `transformers`, `coqui-tts` |
    | `[genai-tts-coqui]` | coqui-tts 0.28.0, torchaudio 2.11.1, transformers 5.22.2, torch 2.15.0 | — |

    **If you use `TextToSpeech` (XTTS)**, replace `[genai-audio]` with
    `[genai-audio,genai-tts-coqui]` (or just `[genai-tts-coqui]`, without
    STT).

<a id="genai-audio-transformers-bound"></a>

!!! check "Since 0.342.0 the Coqui extra no longer caps `transformers<5`"
    Up to 0.341.0 the extra carried `transformers<5`: `coqui-tts` 0.27.5
    imported `isin_mps_friendly`, a symbol transformers 5.1 removed. That held
    anyone installing the extra on transformers 4.57.6, and with it
    `huggingface-hub`, `tokenizers` and, next to `[genai-image]`, `diffusers`.
    `coqui-tts` 0.28.0 no longer imports the symbol, and the extra now requires
    that version.

    Resolved with `uv pip compile` (Python 3.12) on 2026-11-01, while Coqui
    was still in `[genai-audio]` (it is in `[genai-tts-coqui]` today, see
    above), the extra held nothing back any more:

    | Extras | `transformers` | `huggingface-hub` | `tokenizers` | `diffusers` |
    | --- | --- | --- | --- | --- |
    | `[genai-audio]` | 5.22.1 | 2.4.0 | 0.23.3 | — |
    | `[genai-audio,genai-image]` | 5.22.1 | 2.4.0 | 0.23.3 | 0.42.1 |
    | `[genai-image]` without `[genai-audio]` | — | 2.4.0 | — | 0.42.1 |

    The extra declares `six` because `coqui-tts` 0.28.0 imports it without
    declaring it: in a clean venv without it, `import TTS.api` dies with
    `ModuleNotFoundError: No module named 'six'`.

!!! warning "The SDK ships no database driver by default"
    `sqlalchemy[asyncio]` is core, but the async DBAPI is your deploy
    choice: install `[sqlite]` (`aiosqlite`, dev default) or `[postgres]`
    (`asyncpg`, production). Without one, the engine raises
    `ModuleNotFoundError` for the driver on first connection. Services
    scaffolded with `tempest new` already pin both `aiosqlite` and
    `asyncpg` in `pyproject.toml` — the latter is what the generated
    `docker-compose.yaml`'s `prod` profile uses.

!!! info "Lazy imports"
    Since 0.7.1 every optional dependency is imported lazily at first instantiation, so `import tempest_fastapi_sdk` works even when only a subset of extras is installed. Instantiating a helper whose extra is missing raises `ImportError` with a clear hint pointing at the right extra. Since 0.326.0 the SDK's own modules load on demand too: `import tempest_fastapi_sdk` loads one module instead of 269 plus SQLAlchemy, FastAPI, Alembic, Jinja2 and bcrypt, and `tempest --help` went from 0.94 s to 0.14 s (best of 5, measured on this repository). `from tempest_fastapi_sdk import X` and strict type checking work as before.

<a id="genai-audio-gpu"></a>

### GPU with `[genai-audio]`

`[genai-audio]` transcribes with **faster-whisper**, which runs on
**CTranslate2**. The `ctranslate2` 4.x wheels are linked against **CUDA 12**
and, on the first transcription with `device="cuda"`, look up
`libcublas.so.12`. The `torch` PyPI ships since 2.14 is a **CUDA 13** build:
it installs the `.so.13` libraries, not the `.so.12` ones. With the default
resolution, then, the first GPU transcription dies like this:

```text
RuntimeError: Library libcublas.so.12 is not found or cannot be loaded
```

The way out is to install `torch` from PyTorch's **CUDA 12** index, which
brings CUDA 12 cuBLAS and cuDNN as `nvidia-*-cu12` packages.

#### Step 1: `torch` from the cu126 index

In your service's `pyproject.toml`, declare the index as `explicit` (only the
packages you point at it come from it) and point `torch` there:

```toml
[project]
name = "my-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "tempest-fastapi-sdk[genai-audio]>=0.349.0",
    "torch>=2.14.0",
]

[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cu126" }
```

`torch` is listed in `dependencies` even though the extra already brings it:
`[tool.uv.sources]` only applies to a **direct** dependency. Then `uv sync`.

!!! note "Also using Coqui (`[genai-tts-coqui]`)?"
    PyPI's `torchaudio` is a CUDA 13 build too, and it does not load next to a
    CUDA 12 `torch` (`libcudart.so.13: cannot open shared object file`).
    Point it at the same index — and, again, as a direct dependency:

    ```toml
    [project]
    name = "my-service"
    version = "0.1.0"
    requires-python = ">=3.12"
    dependencies = [
        "tempest-fastapi-sdk[genai-audio,genai-tts-coqui]>=0.349.0",
        "torch>=2.14.0",
        "torchaudio>=2.11.0",
    ]

    [[tool.uv.index]]
    name = "pytorch-cu126"
    url = "https://download.pytorch.org/whl/cu126"
    explicit = true

    [tool.uv.sources]
    torch = { index = "pytorch-cu126" }
    torchaudio = { index = "pytorch-cu126" }
    ```

    STT-only users do not need these lines: since 0.349.0 `[genai-audio]`
    brings no `torchaudio` ([why](#genai-audio-coqui-split)). Up to 0.348.0
    it did, so on those versions declare `torchaudio` as above even without
    Coqui.

#### Step 2: CTranslate2's `LD_LIBRARY_PATH`

The `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` packages live inside
`site-packages`, outside the dynamic loader's search path. `torch` finds them
on its own; CTranslate2 does not. Point `LD_LIBRARY_PATH` at them before
starting the process:

```bash
SITE_PACKAGES="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
export LD_LIBRARY_PATH="$SITE_PACKAGES/nvidia/cublas/lib:$SITE_PACKAGES/nvidia/cudnn/lib:${LD_LIBRARY_PATH:-}"
```

In a `Dockerfile`, write the image's venv path, for example
`ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib`.

!!! info "Why it sometimes works without `LD_LIBRARY_PATH`"
    When the process imports `torch` before loading Whisper, `torch` leaves
    CUDA 12 cuBLAS and cuDNN loaded, and CTranslate2 reuses them. That is
    what happens with `SpeechToText(device="auto")`, which imports `torch` to
    detect the GPU, or after a `probe_hardware()`. With an explicit
    `device="cuda"`, nothing imports `torch` first, and without the variable
    the transcription fails. Export the variable anyway: relying on import
    order is fragile.

    On a host with CUDA 12 installed system-wide (`libcublas.so.12` in
    `ldconfig`), neither failure in this section shows up — the loader finds
    the system library. A `python:3.12-slim` container has none.

#### Step 3: check it

Generate a short WAV (here with `espeak-ng`) and transcribe it on the GPU:

```bash
espeak-ng -v en -w hello.wav "The quick brown fox jumps over the lazy dog."
```

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText


async def main() -> None:
    """Transcribe a short WAV on the GPU."""
    stt = SpeechToText("small", device="cuda")
    with open("hello.wav", "rb") as handle:
        result = await stt.transcribe(handle.read())
    print(stt.device, result.text)


asyncio.run(main())
```

```text
cuda The quick brown fox jumps over the lazy dog.
```

`probe_hardware()` also flags the wrong pair before the first transcription:
with a CUDA 13 `torch` and `ctranslate2` installed, `HardwareInfo.warnings`
says what will fail and points to this section (see
[Probing the hardware](recipes/genai.md#probing-the-hardware)).

!!! check "What was measured"
    **In this repository**, on 2026-11-10, in a `python:3.12-slim`
    container with `--gpus all` on an RTX 4070 Ti SUPER (WSL2, Windows driver
    591.86), Python 3.12, `faster-whisper` 1.2.1 and `ctranslate2` 4.8.5,
    model `small` in `float16`, transcribing the WAV above:

    | `torch` | `LD_LIBRARY_PATH` | `torch` imported first | result |
    | --- | --- | --- | --- |
    | 2.15.0+cu132 (PyPI) | no | no | `libcublas.so.12 is not found` |
    | 2.15.0+cu132 (PyPI) | no | yes | `libcublas.so.12 is not found` |
    | 2.15.0+cu132 (PyPI) | `nvidia/{cublas,cudnn}/lib` | no | `libcublas.so.12 is not found` |
    | 2.14.1+cu126 (cu126 index) | no | no | `libcublas.so.12 is not found` |
    | 2.14.1+cu126 (cu126 index) | no | yes | transcribes |
    | 2.14.1+cu126 (cu126 index) | `nvidia/{cublas,cudnn}/lib` | no | transcribes |

    The rows above call `WhisperModel` directly; with the SDK installed on
    top, `SpeechToText(device="cuda")` gave the same pair (fails without the
    variable, transcribes with it) and `SpeechToText(device="auto")`
    transcribed without it. The venv was built from the step 1
    `pyproject.toml` with `tempest-fastapi-sdk[genai-audio]` swapped for
    `faster-whisper`, which is what the extra brings, and the branch's SDK
    installed on top. The audio was the WAV above converted to 16 kHz with
    `ffmpeg`; the original WAV (22,050 Hz) gave the same text through
    `SpeechToText` on the CPU. On the WSL2 host outside the container, which
    has the system `libcublas.so.12` in `ldconfig`, the cu126 venv
    transcribed without the variable and without importing `torch` — which
    is why the table comes from the container.

    **In tempest-ai** (issue #723; `python:3.12-slim`, RTX 4070 Ti SUPER, SDK
    0.348.0) the same fix was applied to the service, with
    `torch==2.14.1+cu126` and `torchaudio==2.11.0+cu126` from the cu126
    index: `BarkSpeech` (`suno/bark-small`) synthesized Portuguese — 5.95 s
    of audio in 9.6 s and 16.76 s in 23.6 s, with the model already loaded.

    **Not measured:** Coqui (`[genai-tts-coqui]`) on the GPU with the cu126
    `torchaudio` in this repository, and other GPUs, drivers or CUDA 12.x
    besides 12.6.

**Recap:** faster-whisper needs CUDA 12; PyPI's `torch` is CUDA 13. Take
`torch` (and `torchaudio`, if you use Coqui) from the cu126 index, as a
direct dependency, and export `LD_LIBRARY_PATH` to
`nvidia/{cublas,cudnn}/lib`.

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
tempest fix                    # ruff check --fix-only . + ruff format . + ruff check .
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
| 3.14 | Supported |
| 3.13 | Supported — the version the repository's `.python-version` pins |
| 3.12 | Supported (minimum) |
| 3.11 | Not supported since 0.314.0 — stays on 0.313.x ([migration](migration.md)) |
| 3.10 and older | Not supported (uses ``X | None`` PEP 604 syntax) |
