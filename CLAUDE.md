# CLAUDE.md — tempest-fastapi-sdk

Project-specific guidance for Claude Code working in this repository.
The global instructions at `~/.claude/CLAUDE.md` apply too — this file
only documents what is *different* or *load-bearing* for this SDK.

## What this is

`tempest-fastapi-sdk` is a **PyPI-distributed library**, not a
deployable service. It ships the shared FastAPI/SQLAlchemy/Pydantic
building blocks every Tempest service imports.

Two structural consequences:

- **Flat layout.** The package directory `tempest_fastapi_sdk/` lives
  at the repo root, next to `pyproject.toml`. **No `src/` wrapper.**
  Tests live in `tests/` at the root. This contradicts the
  service-layout rule in the global `CLAUDE.md` on purpose — detecting
  a `src/tempest_fastapi_sdk/` directory is a defect, flag it before
  adding features.
- **Every public surface change ships docs in the same commit.**
  README install snippets, `CHANGELOG.md`, the MkDocs site under
  `docs/` (bilingual PT-BR + EN-US), and the API reference must all
  reflect the new shape **before** the `vX.Y.Z` tag is pushed. See
  the "Documentation must follow the code" section in the global
  `CLAUDE.md`.

## Release flow

```bash
# 1. bump version
sed -i 's/version = "X.Y.Z"/version = "X.Y.Z+1"/' pyproject.toml
sed -i 's/__version__: str = "X.Y.Z"/__version__: str = "X.Y.Z+1"/' tempest_fastapi_sdk/__init__.py

# 2. CHANGELOG entry under ## [X.Y.Z+1] — YYYY-MM-DD (Keep a Changelog format)

# 3. update relevant docs/recipes/*.md (and the .en.md mirror)

# 4. gate
UV_PYTHON=3.11 make check                 # ruff + mypy + 661+ tests
UV_PYTHON=3.11 uv run --group docs mkdocs build --strict
UV_PYTHON=3.11 make smoke                 # import-test the wheel

# 5. commit + tag + push
git add -A && git commit -m "feat: vX.Y.Z+1 — <subject>"
git tag vX.Y.Z+1
git push origin main && git push origin vX.Y.Z+1
```

CI on tag push runs `release-pypi.yml` (trusted-publishing — no
token), then `docs.yml` redeploys GitHub Pages. Don't push a tag
without the docs being green.

**Docs-only change skips all of this.** Touched only `docs/`,
`README.md` or `CLAUDE.md` prose (no `tempest_fastapi_sdk/**` delta)?
No version bump, no CHANGELOG entry, no tag — commit `docs: <subject>`
and push straight to `main` (rebase on `origin/main` first if behind).
`docs.yml` triggers on the `main` push and redeploys Pages by itself.
Gate is just `uv run --group docs mkdocs build --strict` +
`pytest tests/test_docs_api_guard.py tests/test_docs_organization.py`;
the full `make check` is unnecessary because no Python changed. See
"Docs-only change" in the global `CLAUDE.md` for the reasoning. A
docstring edit that changes a signature or behavior is **not**
docs-only — that follows the flow above.

## Roadmap — features we still owe

The SDK currently covers (Sep 2025+, post-v0.31.x):

- **Auth** — JWT/bearer/role/permission/X-Token deps (JWT deps
  read the token from header → cookie → query string via
  `query_param=`, for cookieless `EventSource`/SSE clients), full
  bundled flow (`UserAuthService` + `make_auth_router` covering
  signup/activate/login/password-reset), `BaseUserModel` +
  `BaseUserTokenModel` (nullable `payload` column carrying flow
  context), email change/re-verify/recovery
  (`request_email_change`/`confirm_email_change`,
  `request_email_verification`/`confirm_email_verification`,
  `request_email_recovery` — password + MFA-if-enrolled, opt-in
  `AUTH_EMAIL_RECOVERY_ENABLED`; old-email security notice via
  `AUTH_EMAIL_CHANGE_NOTIFY_OLD`; `EMAIL_CHANGE` token purpose;
  JSON + backend HTML pages + bilingual templates), OAuth2/OIDC
  providers (`GoogleOAuthClient`, `GitHubOAuthClient`,
  `OIDCProvider`), CSRF middleware + `make_csrf_token_dependency`,
  opt-in DB-backed opaque refresh tokens
  (`BaseUserRefreshTokenModel`, `make_user_refresh_token_model`,
  `refresh_token_model=` on `UserAuthService`) with rotation,
  family-wide reuse detection and `POST /auth/logout`
  (`LogoutSchema`).
- **Permission guards (v0.167.0)** — `@requires(*guards, user_param=None)`
  (`tempest_fastapi_sdk.authz`, re-exported at the root) runs plain
  `(user) -> user | None` guards before a function body, at any layer, sync or
  async; guards deny by raising an `AppException`, and a non-`None` return
  replaces the user the body sees (how `require_active` narrows `UserT | None`).
  The user param resolves from the annotations (`BaseModel`/`BaseUserModel`
  subclass), `user_param=` disambiguates. Misuse caught in three places:
  `TempestPermissionError` at import (no guard, wrong arity, async guard on a
  sync fn, unresolvable user param), `GuardContractWarning` at call time
  (foreign exception, `return False`), and
  `tempest permissions [--check|--strict|--path]` statically (`ast`) — errors
  `no-guards`/`user-param-missing`/`user-param-ambiguous`/`guard-arity`/
  `guard-async-in-sync`/`guard-returns-bool`/`guard-foreign-exception`,
  warnings `guard-never-denies`/`guard-missing-annotation`/`guard-return-type`/
  `guard-unresolved` (ambiguous or out-of-scope guard reported, never guessed).
  `openapi-errors` follows the guards, so their exceptions land in
  `error_responses(...)`. `declared_guards`/`guarded_user_param` introspect a
  route. **Metadata (v0.168.0):** a guard may declare a 2nd param
  `meta: dict[str, Any]` — `meta={...}` literals fixed at decoration plus
  `include_args=True` merging the call's args (user excluded, omitted params
  contribute defaults, `Depends(...)` markers dropped, `meta=` wins a name
  clash); one-param guards untouched; `TempestPermissionError` when `meta=`
  isn't a mapping or nothing consumes it; `guard_metadata(fn)` reads the
  literals; new static codes `meta-unused` (error), `guard-meta-missing`/
  `guard-meta-annotation`/`meta-key-collision` (warnings). Recipe:
  `docs/recipes/permission-guards.md`.
- **DB** — `AsyncDatabaseManager`, `BaseRepository[T]` with
  bulk ops (`bulk_create_values`, `bulk_upsert`, `bulk_update`,
  `add_all`, etc.), `AlembicHelper`, `BaseModel`, audit /
  soft-delete mixins, `reorder_base_columns_first` Alembic
  hook so generated migrations ship `id`/`is_active`/
  `created_at`/`updated_at` first. `alembic.ini` ships with
  `sqlalchemy.url` empty — URL resolves at runtime from env /
  settings / constructor. **Transactions (v0.200.0):**
  `transaction(session)` / `savepoint(session)` (+ `repo.transaction()` /
  `.savepoint()`), depth counter in `session.info` so **every repository on
  that session joins the same block**; `commit()`/`flush()`/`rollback()` on
  the repository (`commit()` degrades to flush inside a block, `rollback()`
  inside one raises), `autocommit=False`. `enable_sqlite_savepoints` is
  applied to every SQLite engine `AsyncDatabaseManager` builds — without it
  `RELEASE SAVEPOINT` **commits** on pysqlite, so test and production
  disagreed about atomicity. **Search (v0.200.0):** `search()` portable
  (escaped ILIKE, AND across words, OR across columns) +
  `full_text_search()` (`websearch_to_tsquery` + `ts_rank` + `setweight` on
  PG, falls back elsewhere; `supports_full_text` reports which);
  `search_condition()`/`full_text_condition()` return the clause and
  `where=` now takes `WhereClause = Q | ColumnElement[bool]`, so a search
  paginates through `paginate()`. Regconfig is inlined, not bound — asyncpg
  cannot infer `regconfig` for a placeholder. **Enum columns (v0.200.0):**
  `TempestEnum` via `BaseModel.type_annotation_map`, so `Mapped[MyEnum]`
  stores the **value**, gets a `CHECK` on SQLite and an `_enum`-suffixed
  native type on PG; `enum_column()` for `default`/`index`. **BREAKING** —
  the previous default stored `.name`. **Enum migrations (v0.200.0):**
  `op.replace_enum` (rename+recreate+cast inside the transaction, defaults
  read from `information_schema` and restored, `value_map=` for renames,
  reversible; SQLite rebuilds via `copy_from` because batch mode otherwise
  carries the stale CHECK), `sync_enum_types` autogenerate hook (compares
  `pg_enum` / the CHECK — autogenerate compares neither), and
  `render_enum_types` (without it **every migration touching an enum column
  failed on import**). Both wired into `env.py.template`. **Query plans
  (v0.200.0):** `explain_queries(session)` / `repo.explain()` → typed
  `ExplainReport`/`QueryPlan`; `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on
  PG, `EXPLAIN QUERY PLAN` on SQLite reported as `PLAN_ONLY` with `None`
  metrics; **writes are never re-analyzed**; plans fetched at driver level
  because `session.execute` applies the wrapped statement's result mapping.
  Recipes: `transactions.md`, `text-search.md`, `enum-columns.md`,
  `query-plans.md`.
- **Standardized exceptions** (`AppException` + subclasses) +
  `register_exception_handlers`. **OpenAPI error docs (v0.160.0):**
  `ErrorResponseSchema` (the `{detail, code, details}` envelope as a
  schema), `error_responses(*exc_classes)` (class-introspected
  `responses=`; groups by status, codes in an `examples` selector since
  OpenAPI allows one response object per status; `catalog=`/`locale=`/
  `descriptions=`), `@raises(...)` + `TempestAPIRouter` (drop-in
  `APIRouter` expanding the tag before route construction, so the model
  lands in `components.schemas`; explicit `responses=` wins per status) +
  `declared_raises`/`RaisesSpec`, `AppException.details_example`,
  `InheritedErrorCodeWarning` (subclass inheriting a **generic** SDK
  `code` — domain codes and `message_key` never warn), and
  `tempest openapi-errors [--check|--allow-unreachable|--path|--fix|--dry-run]`
  (static `ast` walk of router->controller->service->repository reading
  `raise` + `Raises:` sections; reports undocumented/unreachable, CI gate).
  **`--fix` (v0.166.0)** writes the missing declarations back: injects
  `responses=error_responses(...)` into the decorator + the needed imports, or
  appends to an existing `error_responses`/`@raises` call preserving order.
  AST-anchored edits, `ruff` normalized, requires a clean git tree (`--dry-run`
  is read-only and prints the diff). Adds only — `unreachable` is never
  removed, and an ambiguous exception import is reported as `unresolved`
  instead of guessed. Declaring
  `code` in the **class body** is now the documented pattern — the raise-site
  `code=` form is not introspectable.
- **Observability** — structured logging + per-level files +
  `/logs` endpoint, metrics (CPU/RAM/GPU/Disk), Prometheus
  `/metrics` endpoint + `PrometheusMiddleware`, request-id
  middleware with contextvar propagation, typed `HTTPClient`
  (httpx wrapper with retry/backoff/circuit-breaker /
  `X-Request-ID` propagation).
- **HTTP layer** — `RequestIDMiddleware`, `RateLimitMiddleware`,
  `IdempotencyMiddleware` (memory + Redis stores),
  `ResponseCacheMiddleware` (v0.159.0 — ETag/conditional-GET always on +
  opt-in server-side cache via `ResponseCacheStore`/`Memory`/`Redis`; respects
  `no-store`/`private`/`Set-Cookie`, `vary=` key),
  `BodySizeLimitMiddleware`, hardened static files, CORS,
  health + tool-spec routers.
- **Pagination** — offset + cursor.
- **Settings mixins** — every `*Settings` carries
  `title`/`description`/`examples` on every field.
- **SSE** — `EventStream` (bounded queue + `overflow` backpressure —
  `drop_oldest`/`drop_newest`/`block`, `dropped_events` counter,
  `max_queue=0` to disable), `ServerSentEvent`, `sse_response`
  (`on_disconnect=` cleanup), `EventStream.response`, and `SSEBroker`
  (per-channel fan-out; `SSEBroker.response(channel)` bundles
  register + response + unregister-on-disconnect; in-memory
  single-process, or multi-worker via an injected Redis pub/sub bridge
  — same call site).
- **Throttle** — `AttemptThrottle` (any `ThrottleBackend`, e.g.
  `redis.asyncio.Redis`; no in-memory backend bundled).
- **Base CRUD layers** — `BaseService[Repo, Resp, UpdateT]` and
  `BaseController[Service, Resp, UpdateT]` with
  `get_by_id`/`get_or_none`/`list`/`paginate`/`count`/`exists`/`update`/
  `delete`; `update` is partial-aware (PUT/PATCH) and `UpdateT` is an
  optional 3rd generic (defaults to `BaseSchema`, PEP 696).
- **Base enums** — `BaseStrEnum` / `BaseIntEnum` with
  `values`/`keys`/`choices`/`to_dict`/`from_value`/`has_value`/`has_key`.
- **Validated field types** — `tempest_fastapi_sdk.utils` Annotated
  Pydantic types: `PositiveIntField`/`NonNegativeIntField`/`CentsField`/
  `PortField`/`PositiveFloatField`/`NonNegativeFloatField`/`PercentField`/
  `RatioField`/`LatitudeField`/`LongitudeField`/`PriceField`/
  `NonEmptyStrField`/`SlugField`/`HexColorField`.
- **Runtime typing** — `strict_types` / `typed` / `require_annotations`
  decorators (over `pydantic.validate_call`); ruff `ANN` enabled in the
  SDK and `tempest new` templates (ANN401 off — `Any` is valid); a
  `[tool.tempest] typing_strictness` knob (`lenient`/`standard`/`strict`,
  `--strictness` override) layered onto `tempest lint`/`fix`/`type`/`check`.
- **Vision** (`[vision]` extra) — `tempest_fastapi_sdk.vision` wrapping
  `ort-vision-sdk`: lazy `Detector`/`Classifier`/`Segmenter` + prediction
  schemas + `to_detection_schemas`/`to_classification_schema`/
  `to_segmentation_schemas` mappers.
- **Modelops (v0.173.0)** — `tempest_fastapi_sdk.modelops`, two extras
  (`[modelops]` psutil+nvidia-ml-py, `[modelops-onnx]` onnx+onnxruntime); the
  module imports with neither. **Bench:** `benchmark` (any callable) + `benchmark_onnx`/
  `benchmark_torch`/`benchmark_models` — warm-up + N reps, median/IQR first,
  p95/p99, throughput, RSS peak/delta, GPU memory; symbolic dims resolved via
  `dynamic_dims=`/`input_shapes=` or raise (never guessed). **Energy:**
  `PowerSampler` protocol + `NvmlPowerSampler` (prefers the NVML total-energy
  counter, falls back to integrating power), `NvidiaSmiPowerSampler`,
  `RaplEnergySampler` (CPU package via powercap, package domains only,
  wraparound handled), `NullPowerSampler`; `resolve_power_sampler`/
  `resolve_cpu_energy_sampler`; every reading carries an `EnergySource` and a
  CPU run resolves no GPU sampler. **Ranking:** `composite_scores`
  (`DEFAULT_COST_WEIGHTS`, weights renormalized over measured dims twice),
  `pareto_points` (unmeasured axis skipped, cost-only frontier without
  `quality`), `rank` → `BenchmarkReport`. **Export:** `export_torch_to_onnx`,
  `export_onnx_to_ort` (file or dir, FIXED/RUNTIME, `target_platform`, type
  reduction, `.required_operators.config`), `optimize_onnx_graph`.
  **Quantization:** `quantize_onnx_dynamic`/`quantize_onnx_static` plus the
  transformers-export path `optimize_hf_onnx` (`model_type=` override,
  `file_name=` for multi-graph exports) / `quantize_hf_onnx` (arm64/avx2/
  avx512/avx512_vnni; `reduce_range` refused where the ISA cannot saturate),
  and `quantize_hf_bnb`. **No `optimum` dependency** — it capped
  `transformers<4.58` while only wrapping `onnxruntime`, so the tables it
  carried (`_OPTIMIZATION_SPECS`, `_ISA_QUANTIZATION_SPECS`,
  `_ORT_FUSION_MODEL_TYPES`) are ported constants pinned by tests, and
  producing the export is a documented `uvx optimum-cli` step. See the
  "Dependency policy" section in the global `CLAUDE.md`. **Static:**
  `analyze_onnx`/`analyze_ort`/`analyze_torch`/`analyze_model`. CLI
  `tempest model analyze|bench|optimize|quantize|export-ort|hardware`.
  **sklearn to the edge (v0.188.0, `[modelops-sklearn]` = skl2onnx):**
  `export_sklearn_to_onnx` (float32 + ZipMap off), `verify_sklearn_onnx`,
  `edge_bundle` (returns the *smallest* artifact — optimize/`.ort` grow tiny
  graphs), `uses_ml_domain` (int8 quantization does not apply to
  `ai.onnx.ml`). `hummingbird-ml` rejected: caps `onnx<=1.16.1`. **Binary-tree defect relocated (v0.201.0):** it was recorded as a `skl2onnx` conversion bug; holding `skl2onnx` 1.20.0 / `sklearn` 1.9.0 / `onnx` 1.22.0 fixed and moving only the runtime showed it is **`onnxruntime`** — error 1.0 vs `predict_proba` on 1.27.0, 9.5e-08 on 1.28.0. Floor moved to `onnxruntime>=1.28`; `BINARY_TREE_FIXED_IN_ONNXRUNTIME` still gates the export warning for a force-assembled environment.
  **Serving (v0.189.0):** `OnnxPredictor` (resolves input/output names,
  `DEFAULT_INTRA_OP_THREADS = 1` for constrained devices, reports the
  providers *actually* in use, `reload` builds the new session before
  dropping the old so a bad rollout degrades to the previous version),
  `make_prediction_router`, `RegistryModelSource` (fleet update over the
  existing `ArtifactRegistry`, one cached file per version).
  **Monitoring (v0.190.0):** `PredictionMonitor` + `baseline_from_samples`
  + `population_stability_index` — latency/volume, input drift (PSI vs a
  training-time baseline of bin edges only) and prediction distribution;
  constant memory (counters, never rows), per-window, `insufficient_data`
  below `MIN_ROWS_FOR_DRIFT`; PSI thresholds documented as a **convention,
  not a statistical test**. `PredictionMetrics` publishes it to Prometheus;
  `make_prediction_router(monitor=, metrics=)` mounts `GET /monitor`.
  **Edge package (v0.191.0):** `edge_pipeline` (export → verify → drift
  baseline → `manifest.json` + gzip, in one shippable directory),
  `load_edge_package` (predictor + monitor wired, SHA-256 checked),
  `read_manifest`; `EdgeManifest` is a **cross-language contract** with a
  pinned `schema_version` — `tempest-react-sdk/tabular` reads the same file
  in the browser. Measured and documented: graph optimisation is a no-op on
  `ai.onnx.ml`, `.ort` more than doubles the file, int8 does not apply, gzip
  reaches 10-13%; and `DEFAULT_INTRA_OP_THREADS = 1` was **re-justified** —
  the old "coordination costs more than parallelism" claim was not
  measurable; the real reason is oversubscription across concurrent
  requests. Recipe: `docs/recipes/modelops.md`.
- **GenAI self-hosted** (`[genai]` extra: transformers+torch+accelerate;
  `[genai-quant]` = bitsandbytes) — `tempest_fastapi_sdk.genai`, delivered
  in slices. **Shipped (v0.96):** hardware capacity check — `probe_hardware`
  → `HardwareInfo` (CPU/RAM/CUDA-VRAM/MPS/disk, degrades without
  psutil/torch), `can_run`/`recommend` → `CapacityReport` (fits? device,
  estimate vs available, suggestion to quantize/offload), `estimate_model_bytes`/
  `bytes_per_param`/`fetch_num_params` (Hub metadata, no weight download),
  `ModelDtype`. Capacity fns import WITHOUT the extra. **Shipped (v0.98) —
  `TextGenerator`**: local causal LM (`generate`/`chat`/`stream` async via
  to_thread), auto device/dtype, int8/int4 quant (BitsAndBytesConfig), lazy
  `load` + `unload`/`unload_if_idle` (+ `idle_unload_seconds`),
  `resolve_device`/`auto_dtype_name`; torch/transformers lazy. **Shipped
  (v0.99) — embeddings + scale:** `Embedder` (local text→vectors, mean
  pooling, batched, optional `EmbeddingCache`/`InMemoryEmbeddingCache`),
  `BatchScheduler` (coalesce concurrent calls into one batch — pure
  asyncio, no extra), `ModelRegistry` (LRU model sharing with unload).
  Classes-only (no bundled router). Submodule import like
  queue/tasks/vision. **Refinements (v0.100):** `WebSearch.retrieve`
  (one-shot search→extract→context), `ContentExtractor.extract_many`
  (bounded concurrent), generic `chunk_text` (any string), `Embedder(
  normalize=True)` + `cosine_similarity` (semantic search). **Corpus RAG
  (v0.101):** `VectorStore` Protocol + `InMemoryVectorStore` +
  `PgVectorStore` (pgvector, reuses the service Postgres) + `Retriever`
  (`index`/`search`/`retrieve` tying `Embedder` → store → `build_context`);
  `Chunk.score`. **Audio (v0.102, `[genai-audio]` = faster-whisper +
  coqui-tts):** `tempest_fastapi_sdk.genai.audio` — `SpeechToText`
  (faster-whisper transcribe → `Transcription`) + `TextToSpeech` (Coqui TTS
  synthesize → WAV bytes, XTTS voice cloning via `speaker_wav`), lazy +
  to_thread + semaphore, auto device/compute. **Language presets (v0.103):**
  `Language` enum (PT_BR/EN_US) + `preset_for`/`TextToSpeech.for_language`
  — resolves Whisper code + default TTS model per language;
  `transcribe`/`synthesize` accept the enum, a raw code, or None. **Shipped (v0.97) — RAG context**
  (`tempest_fastapi_sdk.genai.rag`, `[genai-rag]` extra = httpx +
  trafilatura + pymupdf): `WebSearchBackend` Protocol + `SearxngBackend`
  (SearXNG JSON API, leviathan pattern) + `WebSearch`; `ContentExtractor`
  (trafilatura, failures never raise); `PdfReader` (PyMuPDF detailed
  extraction → `Document`/`Chunk`, `read`/`chunks` with overlap);
  `build_context(question, sources)` → prompt block mixing web +
  PDF. All import lazily.
- **GenAI self-hosted roadmap (v0.139–0.154).** Shipped on top of the
  above: seed/stop honored on the transformers path (v0.139); retry/
  backoff/circuit-breaker on Ollama + SearXNG via `HTTPClient` (+ its
  `stream()`/`transport=`) (v0.140); **tool calling** on `TextGenerator`
  (`chat_with_tools`, chat-template `tools=`) closing the pipeline gap
  (v0.141); **structured output** `generate_structured` + `parse_structured`
  (Ollama `format=`, transformers `lm-format-enforcer` via
  `[genai-structured]`) (v0.142); **`VisionTextGenerator`** local VLM
  (`[genai-vlm]`) (v0.143); RAG **`Reranker`** cross-encoder (v0.144) +
  **`HybridRetriever`** BM25+dense RRF (`reciprocal_rank_fusion`, `rank-bm25`
  in `[genai-rag]`) + `SupportsRetrieve` (v0.145/0.154); **`OnnxEmbedder`**
  torch-free embeddings (`[genai-onnx]`) (v0.146); prompt→completion
  **generation cache** (`InMemory`/`RedisGenerationCache`, deterministic-only)
  (v0.147); **token/context** (`count_tokens`/`truncate_messages`) (v0.148);
  **`make_vision_router`** (v0.149); **`GenAIMetrics`** Prometheus (v0.150);
  content **moderation** (`RuleModerator`/`ClassifierModerator`) (v0.151);
  and integration — `AIChatPipeline` moderation + context truncation (v0.152),
  metrics+cache on `TextGenerator`/`Embedder` (v0.153); **OTel spans**
  (`genai_span`) — ambient tracing on `generate`/`chat`/`embed`/RAG reusing the
  `setup_tracing` `TracerProvider` (GenAI semconv; no-op without `[otel]`)
  (v0.156). The OpenAI-compatible
  client was **deliberately skipped** (self-hosted-only). Test tiers
  (unit/`@model`/`@gpu`) + plans live under `planning/genai/`. **Fix:** `httpx`
  + `email-validator` are base deps so a minimal/`[genai]` install imports
  (v0.151.1).
- **GenAI weight lifecycle (v0.176.0)** — `tempest_fastapi_sdk.genai.hub`,
  extra `[genai-hub]` (`huggingface-hub` alone; contained in `[genai]`, and
  the module imports with neither). `ModelRef` carries the weight identity
  (id/revision/cache/token/`local_files_only`/`trust_remote_code`) and emits
  **only non-default** kwargs, so an unpinned call stays byte-identical and the
  same dict works with narrower loaders. `resolve_revision` (branch → commit
  sha, `None` when unreachable — never raises), `model_disk_bytes` (Hub
  metadata, no download), `download_model` (`allow`/`ignore` globs →
  `ModelSnapshot`; refuses with `OSError` when free space < estimate x1.1),
  `list_cached_models`/`cache_size_bytes`/`remove_cached_model` (by sha or ref
  name, `dry_run`, `0` for absent = no-op). All 5 transformers loaders
  (`TextGenerator`/`Embedder`/`VisionTextGenerator`/`ClassifierModerator`/
  `Reranker`) take `revision=`/`local_files_only=`/`trust_remote_code=`;
  `SpeechToText` maps onto faster-whisper's `download_root`/`use_auth_token`
  and has no `trust_remote_code` (CTranslate2 runs no repo Python);
  `OnnxEmbedder` pins its tokenizer only (`tokenizer_revision=`/`hf_token=`).
  CLI `tempest model pull|cache-list|cache-rm`. Recipe:
  `docs/recipes/model-weights.md`.
- **Image generation (v0.177.0)** — `tempest_fastapi_sdk.genai.image`, extra
  `[genai-image]` (`diffusers`+`pillow`; module imports without it). Closes
  the last missing generative modality. `ImageGenerator` mirrors
  `TextGenerator` (device/dtype resolution, lazy load, `unload_if_idle`, the
  Hub pinning keywords): `generate(prompt, config=)` → `list[GeneratedImage]`
  carrying the **seed** (drawn when unset, so every result is reproducible),
  `edit(prompt, image, strength=)` via `AutoPipelineForImage2Image.from_pipe`
  (reuses the loaded UNet/VAE/text encoders — no second copy of a ~7 GB
  pipeline), `.pipeline` escape hatch for scheduler/LoRA.
  `ImageGenerationConfig` renames to the diffusers spellings
  (`steps`→`num_inference_steps`, `num_images`→`num_images_per_prompt`) and
  forwards only what is set — turbo wants 4 steps/guidance 0.0, full SDXL
  wants 30/7.5. `max_concurrent=1` by default (one diffusion call saturates
  the GPU; two double peak VRAM). `make_genai_router(image_generator=)` →
  `POST /image` returning the encoded bytes + `X-Image-Seed`. **Dependency
  note:** `diffusers` declares `httpx<1.0.0` + `huggingface-hub<2.0` —
  inert today and confined to the optional extra, accepted because
  schedulers/pipelines/VAE are real engineering, not a preset table.
  **v0.178.0** added `pipeline_kwargs=` (extra `from_pretrained` keywords,
  applied last so they beat the computed ones) after real-model validation
  showed the load itself takes decisions `.pipeline` cannot express —
  `safety_checker=None` (SD 1.x/2.x bundle an extra CLIP), `variant="fp16"`,
  `use_safetensors=True`. Recipe: `docs/recipes/image-generation.md`.
- **Runtime model inventory (v0.179.0)** — `tempest_fastapi_sdk.genai.inventory`
  (pure Python, no extra). `describe_model(handle, key=)` → `LoadedModel`
  reading **attributes only** (never triggers a load; unknown fields are
  `None`, never guessed; a handle exposing only `is_loaded` still appears);
  `LoadedModel.idle_past_threshold` is `False` whenever any input is unknown.
  `runtime_report(models, probe=)` → `ModelRuntimeReport` sorted **loaded
  first, longest-idle first** next to `probe_hardware()`. `ModelRegistry`
  gained `.inventory()` / `.items()` / `.unload_idle()` — the last frees
  weights but **keeps the entry** (it reloads on next use), unlike
  `evict()`. `GET /models` via `make_genai_router(models=)`.
  `ClassifierModerator` / `SpeechToText` / `OnnxEmbedder` gained the uniform
  `seconds_idle` / `unload_if_idle` / `unload` they lacked. **Fix:** the
  router's guard tested truthiness, so an empty `ModelRegistry` (`__len__`
  == 0 — the startup state) read as "nothing injected"; it now tests
  `is None`. **v0.180.0** wired the inventory into the two surfaces someone
  already watches: `GenAIMetrics.observe_inventory(report)` publishes
  `genai_models_loaded{kind,device}` / `genai_models_known` /
  `genai_gpu_vram_free_bytes{index}` (labelled series **cleared per call** —
  a gauge is a snapshot, so an unloaded model must stop being reported), and
  `make_model_cards(models, include_vram=)` (`genai/admin.py`) returns
  `AdminSite(dashboard_cards=)` entries reading the handles at **render**
  time. Also closed the `docs/reference.md` hole: the top-level `genai`
  surface + `genai.rag` + `genai.audio` now render (269 symbols), where
  before only the three new submodules did.
- **Agents (v0.181.0)** — `tempest_fastapi_sdk.agents`, submodule import, **no
  extra**. Goal in, traced run out — the split from `AIChatPipeline` (which
  answers a chat *turn*). `Agent.run/stream` → `AgentRun` (output + `steps` +
  `artifacts` + `stop_reason`); `AgentBudget` bounds steps/wall-clock/tool
  calls and `StopReason` names which fired (`succeeded` is `COMPLETED` only —
  a truncated run still carries text). **Three deliberate properties:** a
  raising tool becomes an observation fed back to the model, never a crashed
  run; every ceiling is enforced *and reported* (`max_seconds` defaults to 120
  because steps alone do not bound a hung call); binary results never enter
  the prompt (`ToolResult` = text for the model + `AgentArtifact` bytes for
  the caller). Handlers take **two** positionals `(arguments, context)` — the
  `AgentContext` holds artifacts **by name**, which is what chains multimodal
  work (draw `bike.png`, then have the VLM describe `bike.png`, no disk, no
  base64); `require_artifact` lists what exists so the model can self-correct.
  Builtin tools over the local models: `generate_image_tool`,
  `describe_image_tool`, `transcribe_audio_tool`, `speak_tool`,
  `retrieve_tool`, `web_search_tool`, `save_artifact_tool`, `text_tool`;
  `AgentTool.from_tool` adapts a pipeline `Tool`. Persistence **opt-in**:
  none → `InMemoryAgentRunSink` (bounded — runs carry artifacts) →
  `BaseAgentRunModel`/`make_agent_run_model`/`DbAgentRunSink` (keeps the trace
  and artifact *names*, not bytes). `make_agent_router` (`POST /run`,
  `/run/stream` SSE + `done`, `GET /runs`, artifact download with a real media
  type). Run state lives in a local `_RunState`, never on `self` — two
  concurrent runs on one agent must not mix. Recipe:
  `docs/recipes/agents.md`.
- **Multi-agent + loops (v0.182.0)** — delegation with **no team object**: an
  agent already picks tools by name, so `agent_tool(agent)` /
  `team_tools({agent: description})` make a specialist *a tool*. Three guards
  a plain tool does not need: the **clock is inherited**
  (`AgentContext.deadline` is absolute; each run takes the **earlier** of its
  own budget and the inherited one, so a child never outlives the request its
  parent holds open), **depth is bounded** (`max_depth`, default 3, turns
  A->B->A into a readable refusal), and the **child's work returns**
  (artifacts namespaced `<agent>/<name>`; a truncated child comes back as
  `[stopped: …] …` instead of passing partial work as complete). New
  `StepKind.AGENT` + `AgentStep.children`/`.agent`/`.total_steps` — a
  delegation can cost as much as a whole run and must not read as a function
  call. **Loops:** `run_until(agent, goal, until=)` repeats until a predicate
  *you* wrote accepts (real Python — parse it, import it, call it — a far
  harder gate than asking the model); `refine(worker, critic, goal)` is
  generate-critique-revise, critic approves with the exact token `APPROVED`
  and never rewrites. `LoopResult`/`LoopIteration` keep every round;
  `accepted=False` means nothing passed. **Fixed here:** the effective
  deadline was written to the run state but not back to the `AgentContext`,
  so delegation handed the child `None` and it ran to its own budget.
- **SSR** (`[ssr]` extra) — `tempest_fastapi_sdk.ssr`: typed Python
  pages rendered to HTML via `tempestweb`'s `render_to_html` /
  `render_document`. `Page` (typed `Component` base — `body()` +
  overridable `shell()` layout), `html_response` (widget tree →
  `HTMLResponse`, full document or bare HTMX fragment), and
  `make_htmx_router` (serves a wheel-bundled HTMX 2.x locally, no CDN).
  `tempestweb` imported lazily so `import tempest_fastapi_sdk` never
  needs the extra.
- **Geolocation (v0.104, `[geo]` extra = httpx)** — `tempest_fastapi_sdk.geo`,
  distance + travel-time between two points with no paid API. Two layers over
  shared schemas (`Coordinate`, `TravelEstimate`, `TravelMode` CAR/MOTORCYCLE/
  BUS): offline heuristic — `haversine_km` (great-circle) + `estimate_travel`
  (road = Haversine x circuity factor; time = car avg speed x mode factor,
  `source="heuristic"`, zero deps/network); real routing — `RoutingBackend`
  Protocol + `OSRMBackend` (free OSRM demo/self-host, injected
  `httpx.AsyncClient`, `source="osrm"`). Moto/bus derive from car via
  `DEFAULT_MODE_DURATION_FACTORS` so both layers work on a car-only profile.
  Submodule import like vision; heuristic imports without the extra.
  **Expanded (v0.106):** offline geometry (`bounding_box`, `within_radius`/
  `nearest` with `key=`, `initial_bearing`/`destination_point`,
  `point_in_polygon`/`polygon_area_km2`, `path_length_km`); DB radius search
  (`GeoPointMixin` + `GeoRepositoryMixin.nearby` bbox-prefilter+haversine,
  `PostGISRepositoryMixin.nearby` ST_DWithin, `make_geo_point_model`);
  geocoding (`GeocodingBackend`/`NominatimBackend` + `GeocodeResult`);
  `OSRMBackend.matrix` (table → `DistanceMatrix`) + `route(with_geometry=True)`
  (decoded into `TravelEstimate.geometry`) + per-mode `DEFAULT_MODE_PROFILES`;
  polyline codec (`encode_polyline`/`decode_polyline`); `TravelMode.BICYCLE`/
  `PEDESTRIAN`; BR `uf_centroid`/`UF_CENTROIDS` (offline) + `cep_to_coordinate`.
- **GenAI ergonomics (v0.105)** — `GenerationConfig` (typed generation
  params over `**kwargs`, `config=` on `generate`/`chat`/`stream`),
  `make_genai_router` (opt-in FastAPI router mounting only injected
  objects: `/generate`+SSE, `/chat`, `/embed`, `/rag`, `/transcribe`,
  `/tts`), `RedisEmbeddingCache` + `AsyncEmbeddingCache` (async Redis
  vector cache; `Embedder` awaits sync-or-async caches at one call site).
- **Chat (v0.105, `tempest_fastapi_sdk.chat`, no extra)** — threaded
  chat over the SDK primitives: abstract `BaseConversationModel`/
  `BaseConversationParticipantModel`/`BaseMessageModel` + `make_*`
  factories, `ChatService` (`start_conversation`/`post_message`/
  `list_messages`/`list_conversations`/`is_participant`), `make_chat_router`
  (participant guard) + real-time fan-out via an injected `SSEBroker`.
  Submodule import.
- **Reviews (v0.105, `tempest_fastapi_sdk.reviews`, no extra)** —
  comments + 0–5 star ratings on any polymorphic `(target_type,
  target_id)`: `BaseCommentModel` (thread via `parent_id`) / `BaseRatingModel`
  (unique per user) + `make_*`, `ReviewService` (`add_comment`/
  `list_comments`/`rate` upsert/`get_user_rating`/`aggregate` → avg +
  count + per-star distribution), `make_reviews_router`; `RatingField`
  (`Annotated[int, 0..5]`) in `utils.fields`. Submodule import.
- **Upload** — `UploadUtils` with pluggable backends
  (`LocalUploadStorage`, `MinIOUploadStorage`, opt-in injected via
  `backend=`), download helpers, presigned URLs, plus `FileStoreUtils`
  — a unified facade bundling upload + download + presign over one
  shared backend (`uploader`/`downloader`/`backend`/`client` escape
  hatches).
- **MinIO / S3** — `AsyncMinIOClient` via `[minio]` extra
  (bucket lifecycle, object I/O, streaming download, presigned
  URLs).
- **Email** — SMTP via `EmailUtils` + Jinja2 template rendering
  with bundled defaults (`activation.html`, `password_reset.html`)
  shadowable by the project's `template_dir`.
- **WebPush** — `WebPushDispatcher` (`send`/`send_many`, 404/410
  pruning), subscription storage (`BaseWebPushSubscriptionModel` +
  `make_web_push_subscription_model`) + `WebPushSubscriptionService`
  (`subscribe`/`unsubscribe`/`list_for_user`/`notify_user` with
  auto-prune of gone endpoints) + `make_web_push_router` (opt-in
  `/subscribe` + `/unsubscribe`, aligned with `tempest-react-sdk`);
  webhook signatures.
- **Cache** — Redis manager + `@cached`.
- **Queue / tasks** — typed facades hiding FastStream + TaskIQ:
  `MessageBroker` (`.rabbitmq`/`.redis`/`.kafka`/`.nats`, `@mq.on(channel)`
  consumer, channel-first `publish(channel, message)`, `.broker` escape
  hatch) and `TaskQueue` (`.rabbitmq`/`.redis`/`.memory`, `@tq.task` →
  `Task.enqueue`/`.run`, folded `@tq.cron`/`@tq.interval` +
  `start_scheduler`, `tq.broker`/`tq.scheduler` for the CLIs). **Both
  decorator and class-based styles**: `Consumer` + `@subscribe` +
  `MessageBroker.register` (constructor form takes explicit
  `channel`+`schema`, no magic); `TaskDef` + `@task_method` +
  `TaskQueue.register`. **Class-based publish (v0.208.0):**
  `Publisher[T]` + `MessageBroker.publisher_for` — the symmetric half
  `Consumer` never had. Declares `channel` (`str | QueueSpec`) + `schema`
  as class attributes; `publish` **takes the declared type**, enforces the
  schema on the way out (the consumer is a process away and can only
  reject what already left), and registers a spec's topology so a
  producer-only service still declares the DLX it names. Goes through
  `MessageBroker.publish`, not FastStream's publisher object, so it keeps
  the `message_id` dedup needs and the tracing headers — the raw object
  would look identical and lose both. `Consumer`/`@subscribe`/
  `Subscription` also had `channel: str` while `register` bound
  `str | QueueSpec`, so the class path could not declare topology without
  failing the type checker. **Class-path parity (v0.209.0):** `prefetch`
  is now a named keyword on `subscribe()` and `Consumer.__init__`, plus a
  class attribute covering every binding (a `@subscribe` naming its own
  wins) — `register` translates it into the FastStream `Channel` exactly
  as `on()` does. FastStream has **no** `prefetch` keyword, so the class
  path raised `TypeError` where the decorator worked. Found against a
  real broker and pinned there: `rabbitmqctl list_consumers queue_name
  prefetch_count` reports the caps. The constructor-form `Consumer` also
  forwards `**options` (`exchange=` etc.) — it registered `options={}`
  while `@subscribe` had taken them all along. **Cron without syntax**:
  `Cron`/`CronOffset`
  (`BRASILIA` etc.)/`Weekday` enums + `daily`/`weekdays`/`every_n_minutes`/
  `weekly`/`monthly`/… builders (dependency-free). `AsyncBrokerManager`
  renamed to **`AsyncQueueManager`** (v0.94.0; old alias kept); legacy
  `AsyncTaskBrokerManager`/`AsyncTaskScheduler` kept. Outbox
  (`BaseOutboxModel`/`OutboxRelay`/`save_with_outbox`) plugs its `publish`
  into `MessageBroker.publish`. **Task reliability + observability (v0.157.0):**
  `RetryPolicy` + `TaskQueue.enable_retries` (TaskIQ `SimpleRetryMiddleware`),
  `DeadLetter`/`DeadLetterSink` + `TaskQueue.dead_letter` (terminal-failure
  routing, backend-agnostic; `make_dead_letter_middleware`), `TaskMetrics` +
  `TaskQueue.enable_metrics` (`tasks_runs_total`/`tasks_duration_seconds` on the
  shared Prometheus registry). Opt-in middleware, imports without `[tasks]`.
  **Dead-letter panel (v0.158.0):** `BaseDeadLetterModel`/`make_dead_letter_model`
  + `DbDeadLetterSink` (persist terminal failures) + `make_dead_letter_admin_model`
  (read-mostly `AdminModel` + `make_requeue_action`) + `task_inventory`
  (`TaskInfo` per registered task). No live queue introspection (TaskIQ exposes
  none) — shows persisted failures + declared task set.
- **BR validators** — CPF/CNPJ/CEP/phone, with `*Field` Pydantic types
  (`CPFField`/`CNPJField`/`CPFOrCNPJField`/`PhoneBRField`/`CEPField`;
  pre-0.76 unsuffixed names kept as deprecated aliases). **PIX keys**
  (v0.95.0): `PixKeyField` validates+normalizes any of the 5 BACEN key
  types (CPF/CNPJ/email/E.164 phone/random UUID); `PixKeyType` +
  `detect_pix_key_type`/`is_valid_pix_key`/`normalize_pix_key`.
- **BR localities** — `UF` (StrEnum, 27 siglas) + `Region`
  (5 macro-regiões IBGE), `StateBR`/`CityBR` schemas, offline
  dataset of 27 states + 5606 municipalities (IBGE-derived,
  DF as 36 administrative regions), `list_states`/`get_state`/
  `cities_by_uf`/`states_by_region`, `is_valid_uf`/`normalize_uf`,
  `is_valid_city`/`normalize_city` (accent/case-insensitive),
  `UFField`/`CityNameField`, plus `ChoiceBR` + `uf_choices`/
  `region_choices`/`city_choices` (frontend `<select>` choices).
- **Rate limit** — `RateLimitMiddleware` (sliding window) with
  pluggable store (`MemoryRateLimitStore` / `RedisRateLimitStore`,
  atomic Lua) and per-principal key extractors (`key_by_ip`,
  `key_by_jwt_subject`, `key_by_jwt_claim`, `key_by_header`).
- **i18n error envelopes** — `MessageCatalog` +
  `default_message_catalog` (PT-BR + EN), `parse_accept_language`,
  `AppException.message_key` / `message_params`,
  `register_exception_handlers(..., catalog=..., default_locale=...)`.
- **Cache invalidation** — `@cached(namespace=..., tags=...)` +
  `CacheInvalidator` (`invalidate_namespace` / `invalidate_tag` /
  `invalidate_tags` / `invalidate_keys`).
- **Feature flags** — `tempest_fastapi_sdk.flags`: `FeatureFlags`
  over `Memory` / `Env` / `Redis` / `Composite` backends +
  `make_flag_dependency` route guard.
- **Audit trail** — `BaseAuditLogModel` + `AuditAction`,
  `snapshot_model` / `diff_snapshots`, `BaseRepository` opt-in
  (`audit_model=...` + `add_audited` / `update_audited` /
  `delete_audited`, same-tx).
- **Admin panel** — Jinja + HTMX (`AdminSite`, `AdminModel`,
  `make_admin_router`), typed theming via `AdminTheme` (colors /
  logo / favicon / font / radius / footer / dark mode /
  `custom_css_url`, injected as `:root` overrides), custom bulk actions
  (`@admin_action` + `AdminModel(actions=[...])`, `AdminActionContext` /
  `AdminActionResult`), file/image upload fields (`AdminModel(
  upload_fields=[...], upload_storage=...)`), rich list filters
  (bool/enum/FK select, date-range, text — auto by column type).
- **OpenAPI codegen (v0.161.0)** — `tempest_fastapi_sdk.openapi` +
  `tempest openapi-client <spec>`: generates Pydantic schemas **and** a
  typed HTTP client from a third party's OpenAPI 3 spec into
  `<src|app>/integrations/<name>/`. Every `Field` carries the spec's
  `title`/`description`/`examples` (the module doubles as the
  integration's docs); Python names + wire-name `alias` +
  `populate_by_name`; reserved words resolved; optional collections
  default to `[]`; enums → `BaseStrEnum`/`BaseIntEnum`; `allOf`
  flattened; recursion via `model_rebuild()`. Client takes an injected
  `HTTPClient` (so retry/breaker/creds stay with the caller, and
  `httpx.MockTransport` tests it offline). Emitted code passes
  `ruff check` + `ruff format --check` **before** the format pass
  (asserted against raw output), and an unchanged spec regenerates
  byte-identically. Unrepresentable constructs (`not`, external `$ref`,
  Swagger 2.0, non-JSON bodies, header params) → `Any` + a
  `# openapi: unsupported` marker + a line in the command summary —
  never a silent wrong schema. YAML needs `[openapi]` (pyyaml); JSON
  needs nothing.
- **CLI** — `tempest new` (scaffolds layered service +
  docker-compose + multi-stage uv `Dockerfile`/`.dockerignore`),
  `tempest generate --docker` (regen compose) / `--dockerfile`
  (regen Dockerfile + .dockerignore) / `--src` (extra source layers),
  `tempest db init/revision/upgrade/downgrade/current/history/seed`,
  `tempest user create [--admin] / list`, `tempest secrets rotate`,
  `tempest model analyze/bench/optimize/quantize/export-ort/hardware/
  pull/cache-list/cache-rm`,
  plus quality gates (`lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check`), `openapi-errors`, `openapi-client`, `permissions`.

The whole Tier S / Tier A / Tier B backlog that used to live here is
**shipped**, and so is the five-item next-version plan that followed it
(rate-limit per principal, i18n error envelopes, `@cached`
tag/namespace invalidation, feature flags, audit trail — all landed in
v0.54.0–v0.58.0). The covers list above is the source of truth; don't
re-plan finished work.

### Next-version plan

**Theme: Admin panel — close the gap vs Django Admin / Laravel Nova /
SQLAdmin.** The current admin is a complete Phase-1 surface (list /
detail / CRUD, ILIKE search, boolean-only filters, sort, offset
pagination, CSV/JSON export, bulk activate/deactivate/delete, TOTP MFA,
audit stamps, 8 fixed widgets, FK `<select>` capped at 1000 rows).
Competitor admins go further; several of those gaps map to **engines
the SDK already ships but the admin does not surface** — so the work is
mostly wiring, not greenfield.

Build in tiers. Ship each item, document it (same-commit docs rule),
then move it up to the covers list.

**Shipped — `AdminTheme` (v0.72.0).** Typed appearance overrides
(colors / logo / favicon / font / radius / footer / dark mode /
`custom_css_url`) injected as `:root` CSS-variable overrides via
`AdminSite(theme=...)`. This is the "beautiful + typed customization"
foundation the user asked for first; the functional Tier 1 items below
inherit the look for free. Now in the covers list.

**Shipped — custom actions (v0.84.0).** `@admin_action` decorator +
`AdminModel(actions=[...])` + `AdminActionContext`/`AdminActionResult`;
custom entries render in the bulk dropdown (namespaced `custom:<name>`),
run on the checked rows, and flash a banner on the list view. Now in the
covers list.

**Shipped — file / image upload field (v0.85.0).** `AdminModel(
upload_fields=[...], upload_storage=...)` renders String columns as file
inputs, streams the upload to `LocalUploadStorage` / `MinIOUploadStorage`,
and stores the returned key. Now in the covers list.

**Shipped — rich filters (v0.86.0).** `list_filter` fields auto-pick a
widget by column type: bool / enum / FK → select, date/datetime →
inclusive date-range (two inputs → `__gte`/`__lte`), other → text.
Now in the covers list.

**Shipped — the whole Tier 1/2/3 backlog is done** (verified 2026-07-24):

1. **Audit history viewer** — per-row change timeline in the detail view
   wired to `AdminModel(audit_model=...)` + `_format_audit_changes`
   (`BaseAuditLogModel`); template renders the `history` timeline.
2. **Autocomplete FK fields** — HTMX-backed FK inputs (`FieldRef`), past
   the plain-`<select>` cap.
3. **Inlines / nested relations** — `Inline` (edit child rows in the
   parent's detail/edit view).
4. **Dashboard business metrics / charts** — `MetricCard` +
   `MetricValue` / `MetricTrend` / `MetricPartition`
   (`AdminSite(dashboard_cards=[...])`), distinct from the system panel.
5. **RBAC granular** — `AdminPermission` + `AdminAccessPolicy` (per-model /
   per-action), beyond `is_admin` + `can_create`/`can_edit`/`can_delete`.
6. **CSV import** — `AdminModel(can_import=True)` + the CSV import page /
   bulk-create endpoint (counterpart to the export).
7. **Lenses** — `Lens` (saved alternate views / queries per model).

All the above are in the covers list / admin recipe. Origin: competitor gap
analysis (Django Admin, Laravel Nova, SQLAdmin, Starlette-Admin) run
2026-06-26.

**Admin backlog: empty.** No queued admin work — pick the next theme from
business need, not from a stale list. Keep this honest, not aspirational.

## Regra de organização da documentação

**A documentação fica organizada, ordenada e completa nas duas línguas.**
Isso não é revisão de gosto: é regra do projeto, verificada por
`tests/test_docs_organization.py` (roda dentro do `make check`, logo na
CI). Uma página nova não está pronta enquanto os itens abaixo não valem.

### Ao adicionar (ou renomear) uma página

1. **Duas línguas.** `docs/<página>.md` (PT-BR, default) **e**
   `docs/<página>.en.md` (EN-US). Espelho faltando cai em fallback
   silencioso no site.
2. **Dois navs.** A entrada vai no `nav:` de topo **e** no `nav:` do
   locale `en` (dentro do plugin `i18n` no `mkdocs.yml`). O
   `mkdocs-static-i18n` traduz rótulo mas **não reordena** nav
   compartilhado, por isso existem dois — mexer em um exige mexer no
   outro.
3. **Na posição alfabética**, em cada língua, pelo rótulo visível
   (comparação case- e acento-insensível). Vale para a seção
   `Receitas`/`Recipes` e a subseção `Exemplos completos`/`Complete
   examples`.
4. **Índice da landing.** Receita nova entra na tabela de
   `docs/recipes/index.md` **e** `.en.md`, também em ordem alfabética.
5. **Referência.** Símbolo público novo ganha stub em
   `docs/reference.md` (a `reference.en.md` é página-ponteiro, não
   duplica).
6. **Build limpo.** `uv run --group docs mkdocs build --strict` com zero
   warning.

### O que o guard cobre

- espelho `.en.md` para toda página, e nenhuma `.en.md` órfã;
- toda página do disco alcançável pelo nav da sua língua;
- os dois navs cobrindo o mesmo conjunto de páginas, sem duplicata;
- seções alfabéticas alfabéticas **nas duas línguas**;
- tabelas da landing de receitas ordenadas **e** cobrindo toda receita
  do nav.

### O que fica fora da ordem alfabética, de propósito

Abas de topo (`Início → Instalação → Arquitetura → Tutorial → …`),
páginas de `learning/`, a trilha de `getting-started/` (aninhada sob a
aba `Instalação`: uv → versões do Python → primeiro projeto →
documentação oficial) e o tour na landing de receitas seguem **ordem
didática**. Ordenar essas seria a regressão. Fora do nav, a mesma
disciplina vale para listas que o leitor usa como índice: tabela de
módulos do README, tabela de extras da instalação (com `[all]` por
último, por ser catch-all) e os grupos temáticos de
`docs/reference.md` (com `## Superfície de topo` fixa no início). Dentro
da referência, os blocos `###` de módulo e suas entradas `:::` mantêm o
agrupamento por submódulo — ali o agrupamento é a informação.

## Conventions specific to this repo

- **Typed examples in docs.** Every code block in `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` MUST have full
  type annotations (params + return). User explicitly rejected
  "magic Django-style" untyped APIs.
- **Docs/API guard.** `tests/test_docs_api_guard.py` (runs in `make
  check`) asserts every ```python doc block parses and every
  `__all__` name resolves — it catches broken examples and
  renamed/removed exports the docs still reference. It does **not**
  catch *prose* drift (a covers/roadmap line describing something as
  backlog that's actually shipped, or vice-versa). So on every
  feature/release, **re-read the covers list + any roadmap/next-version
  prose in this file against the shipped code** and fix mismatches in
  the same PR — this drifts easily (it happened for both the admin
  tiers and the genai roadmap). Add `# docs-guard: skip` to a doc block
  only for an intentionally non-parseable fragment.
- **Docs signature guard (v0.170.3).**
  `tests/test_docs_signature_guard.py` (also in `make check`) is the
  layer above: it checks every doc example **against the real
  signatures** — keywords exist, positional arity fits (so
  `f(obj, ..., kw=1)` is caught: the literal `Ellipsis` is an
  argument), `from tempest_fastapi_sdk... import X` resolves, and no
  install snippet requires a version above `pyproject.toml`'s. Symbols
  resolve per block from that block's own imports, so the two
  `RetryPolicy` classes (root/HTTP `max_attempts` vs `.tasks`
  `max_retries`) never collide; a symbol used without an import is not
  checked. **Prose is still unguarded**: a sentence promising a
  parameter that does not exist only fails this suite when an example
  passes it, so re-read the prose you write around a signature.
- **`**kwargs` guard (v0.208.0).** `tests/test_kwargs_guard.py` (also in
  `make check`) walks the package with `ast` and fails when a function
  reads a key out of its **own** `**kwargs`/`**options` — `options.pop("x")`
  makes `x` a real parameter the type checker cannot see, the docstring
  stops describing, and an upstream parameter of that name will one day
  collide with. The fix is always to promote it to a named keyword-only
  parameter, which is source compatible. This shipped **five times** in
  `MessageBroker` and survived a manual audit of that exact file, which is
  why it is a test. It does **not** see the subtler form (splatting
  `**options` into a callable whose named parameters absorb keys — how
  `publisher_for` had it), since that needs the callee's signature
  resolved. The suite also asserts the guard **fires** on the shape that
  actually shipped: a guard that cannot fail is one nobody should trust.
  Mark a line `# kwargs-guard: skip` only for a case that is genuinely not
  this, with a docstring saying why. See "`**kwargs` is for passthrough
  only" in the global `CLAUDE.md`.
- **Regra: a documentação fica organizada e em ordem — e isso é
  testado.** Ver a seção "Regra de organização da documentação" acima;
  `tests/test_docs_organization.py` (roda no `make check`) é a
  autoridade.
- **No emojis in code or docs** unless the user explicitly asks.
- **Bilingual docs.** Every page lives twice: `docs/<page>.md`
  (PT-BR, default) and `docs/<page>.en.md` (EN-US). The MkDocs
  `mkdocs-static-i18n` plugin renders both. Forgetting the `.en.md`
  mirror is a structural defect, not a polish item.
- **Bind defaults: `127.0.0.1`** in CLI-generated templates;
  `0.0.0.0` only when a frontend on a different origin consumes
  the service.
- **Logging tests must pass `file_output=False`** to avoid stray
  `logs/` folders in cwd. The default behavior writes to disk
  (since v0.22.0).
- **Explicit re-exports in every `__init__.py`.** Every public
  symbol that an `__init__.py` re-exports MUST use **both**:

  1. The PEP 484 `from x import Y as Y` form (explicit re-export),
     and
  2. A `__all__: list[str]` listing the same symbol.

  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) on different strictness
  settings and without project-aware `pyrightconfig.json`. Either
  form ALONE is theoretically PEP 484 compliant, but in practice
  basedpyright + Pylance strict still flag `from foo import Bar`
  inside an `__init__.py` as "private import usage" unless the
  symbol is aliased with `as Bar`. Always pair the two so any
  IDE — with or without a project config — accepts
  `from tempest_fastapi_sdk.<module> import Symbol` without a
  diagnostic. Example:

  ```python
  # tempest_fastapi_sdk/foo/__init__.py
  from tempest_fastapi_sdk.foo.bar import Bar as Bar
  from tempest_fastapi_sdk.foo.baz import Baz as Baz

  __all__: list[str] = ["Bar", "Baz"]
  ```

  Plain `from tempest_fastapi_sdk.foo.bar import Bar` (without
  `as Bar`) inside an `__init__.py` is a structural defect — flag
  it before adding features. When adding a new public symbol,
  update **both** the import alias and `__all__` in the same
  patch.
