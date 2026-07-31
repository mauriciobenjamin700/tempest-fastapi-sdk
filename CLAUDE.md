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
  settings / constructor.
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
  `TaskQueue.register`. **Cron without syntax**: `Cron`/`CronOffset`
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
