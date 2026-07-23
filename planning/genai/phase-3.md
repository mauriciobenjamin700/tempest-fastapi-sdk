# Fase 3 — Infra + ergonomia

Todos herdam a "Definição de pronto" do plano C. Ordem: #9 → #11 → #12 → #7 → #14.

---

## #9 — cache de geração prompt→completion (v0.147.0)

**Meta:** respostas determinísticas (temp=0 / `do_sample=False`) servidas de
cache, reusando `@cached`/Redis + `CacheInvalidator`. Espelha `EmbeddingCache`.

**Checkpoints**
- [ ] `GenerationCache` Protocol (sync + async) + `InMemoryGenerationCache` + `RedisGenerationCache` (padrão idêntico ao `EmbeddingCache`).
- [ ] Chave = hash(model_id + prompt/messages + gen params determinísticos). Só cacheia quando `do_sample=False` ou `temperature==0` (não-determinístico nunca cacheia).
- [ ] `TextGenerator(generation_cache=...)` / `OllamaGenerator` — await sync-or-async no mesmo call site (como o `Embedder` faz).
- **DoD (camada 1):** 2ª chamada idêntica determinística → 0 chamadas ao backend (fake conta invocações); chamada não-determinística nunca cacheia; `invalidate` derruba a entrada.

**Deps:** independe. **Passos código:** novo `genai/generation_cache.py`, `genai/text.py` + `genai/ollama.py` (wire), `genai/__init__.py`.
**Extras novos:** nenhum (Redis em `[cache]`, já existe). **Docs:** recipe genai-text/ollama seção cache (+`.en.md`); API ref; CHANGELOG `Added`.
**Risco/rollback:** cachear não-determinístico por engano = respostas idênticas erradas. Mitigação: guard explícito + teste. Rollback = `generation_cache=None`.
**Release:** `feat: prompt→completion generation cache (v0.147.0)`.

---

## #11 — token counting + gestão de contexto (v0.148.0)

**Meta:** contar tokens e truncar histórico p/ caber na janela de contexto —
usado por `ChatMemory`/`AIChatPipeline` e exposto ao consumidor.

**Checkpoints**
- [ ] `count_tokens(text, tokenizer) -> int` e `count_message_tokens(messages, tokenizer) -> int`.
- [ ] `truncate_messages(messages, max_tokens, tokenizer, *, keep_system=True) -> list` (dropa turnos antigos, preserva system + turno atual).
- [ ] Integração opt-in no `AIChatPipeline` (trunca antes de montar prompt).
- **DoD (camada 1):** contagem bate `len(tokenizer.encode(...))` em fixtures; truncação respeita `max_tokens` e preserva system + último turno; caso já-cabe = no-op. **DoD (camada 2):** contagem com tokenizer real bate exato.

**Deps:** consumido por #14 (janela p/ moderação de contexto) e melhora #9 (chave estável). **Passos código:** novo `genai/tokens.py`, `genai/pipeline.py` (wire opt-in), `chat`/rag onde couber.
**Extras novos:** nenhum (`[genai]`). **Docs:** recipe genai-context (+`.en.md`) novo + nav + API ref; CHANGELOG `Added`.
**Risco/rollback:** contagem aproximada difere por modelo (BPE vs SentencePiece). Mitigação: sempre usar o tokenizer do modelo em uso, nunca heurística. Rollback = função isolada, sem wire.
**Release:** `feat: token counting + context-window truncation (v0.148.0)`.

---

## #12 — observabilidade genai (v0.149.0)

**Meta:** métricas de inferência (tokens/s, latência, contagem por request,
tokens in/out) expostas via o módulo `metrics` existente + Prometheus.

**Checkpoints**
- [ ] Hooks em `TextGenerator`/`OllamaGenerator`/`Embedder` emitindo: contador de requests, histograma de latência, contador de tokens in/out, gauge tokens/s.
- [ ] Reusar `PrometheusMiddleware`/registry existentes — nomes de métrica namespaced `genai_*`.
- [ ] Opt-in (sem overhead quando não configurado).
- **DoD (camada 1):** N gerações → contador += N, histograma observa N amostras, tokens in/out somam o esperado (backend fake com contagens conhecidas). **DoD (camada 3, manual):** latência real plausível num modelo local.

**Deps:** aproveita #11 (contagem de tokens). **Passos código:** novo `genai/metrics.py` (ou estender `utils/metrics`), wire nos backends, `genai/__init__.py`.
**Extras novos:** nenhum (`prometheus-client` em `[all]`/observabilidade existente). **Docs:** recipe observabilidade genai (+`.en.md`); API ref; CHANGELOG `Added`.
**Risco/rollback:** overhead por-token em stream. Mitigação: medir por-request, não por-token; agregação barata. Rollback = hooks no-op.
**Release:** `feat: genai inference metrics (v0.149.0)`.

---

## #7 — `make_vision_router` (v0.150.0)

**Meta:** router FastAPI opt-in p/ vision, espelhando `make_genai_router` —
monta só os endpoints dos objetos injetados.

**Checkpoints**
- [ ] `make_vision_router(*, classifier=None, detector=None, segmenter=None, prefix="/api/vision", tags=...)`.
- [ ] `POST /classify`, `/detect`, `/segment` (UploadFile) → schemas de `vision/schemas.py` via mappers de `vision/mapping.py`.
- [ ] `ValueError` se nada injetado; só monta o injetado (padrão do `make_genai_router`).
- **DoD (camada 1):** via `TestClient` + detector/classifier/segmenter **fake**, cada endpoint injetado responde 200 + schema correto; endpoint não-injetado ausente (404); nenhum objeto → `ValueError`.

**Deps:** independe. **Passos código:** novo `vision/router.py`, `vision/__init__.py` (re-export duplo — cuidado com o `__getattr__` lazy existente).
**Extras novos:** nenhum (`[vision]`). **Docs:** `docs/recipes/vision.md` (+`.en.md`) seção router + API ref; CHANGELOG `Added`.
**Risco/rollback:** `vision/__init__.py` usa `__getattr__` lazy — router não pode forçar import de `ort-vision-sdk` no import-time. Mitigação: aceitar objetos já-construídos (não construir dentro do router). Rollback = remover export.
**Release:** `feat: make_vision_router (v0.150.0)`.

---

## #14 — moderação / safety (v0.151.0)

**Meta:** filtro opt-in de entrada/saída via classificador local (transformers)
ou regras, plugável no `AIChatPipeline`.

**Checkpoints**
- [ ] `ModerationBackend` Protocol + `RuleModerator` (listas/regex, dep-free) + `ClassifierModerator` (transformers `AutoModelForSequenceClassification`, ex. modelo multilíngue de toxicidade).
- [ ] `Moderator.check(text) -> ModerationResult(flagged, categories, score)`.
- [ ] Hook opt-in no `AIChatPipeline` (checa input antes, output depois; bloqueia ou anota conforme política).
- **DoD (camada 1):** com classificador fake rotulando por regra, input tóxico é `flagged`, limpo passa; política block levanta/substitui, política annotate deixa passar marcado. **DoD (camada 2/3):** classificador real rotula amostras PT-BR do checklist razoavelmente.

**Deps:** usa #11 (janela) e encaixa no pipeline junto de #2/#3. **Passos código:** novo `genai/moderation.py`, `genai/pipeline.py` (hook), `genai/__init__.py`.
**Extras novos:** nenhum (`[genai]`); `RuleModerator` funciona sem extra. **Docs:** recipe genai-moderation (+`.en.md`) + nav + API ref; CHANGELOG `Added`.
**Risco/rollback:** qualidade PT-BR de modelos de moderação é fraca → falsos pos/neg. Mitigação: default = `RuleModerator` (previsível); classificador opt-in documentado como best-effort. Rollback = hook off por default.
**Release:** `feat: content moderation layer (v0.151.0)`.

---

## Encerramento do roadmap

Ao fechar #14, mover todos os itens p/ a lista "covers" do `CLAUDE.md` do repo
(fonte de verdade), e atualizar `memory/genai-selfhosted-roadmap.md` marcando
concluído. Cada item já entregue segue a cadência branch→PR→commit (sem push
até liberação explícita — instrução vigente do usuário).
