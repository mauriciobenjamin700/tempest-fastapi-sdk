# Fase 0 — Correção + hardening

Pré-requisito: `validation-strategy.md` (plano C). Ordem: chore-infra → #5 → #8.

---

## chore — infra de testes genai (antes de #5)

**Meta:** existir as 3 camadas de teste, markers e harness descritos no plano C,
para que #5 em diante tenham onde provar mecânica e comportamento.

**Checkpoints**
- [ ] `markers` (`model`, `gpu`) registrados em `pyproject.toml`; `addopts` exclui os dois por default.
- [ ] `make test-model` / `make test-gpu` existem.
- [ ] `tests/genai/conftest.py` expõe `fake_text_backend`, `stub_httpx`, `tiny_causal_lm`.
- [ ] `tests/genai/_eval/` com corpus fixo + `recall_at_k`/`mrr` puros + teste próprio.
- [ ] `planning/genai/manual-validation.md` criado (template vazio por item).
- **DoD:** `make check` verde com defaults; `uv run pytest -m model` coleta ≥1 teste; suíte default não regride.

**Deps:** desbloqueia todos os itens. **Extras novos:** nenhum. **Docs:** nenhuma (interno).
**Risco:** marker mal registrado quebra `--strict-markers` → rodar `make test` antes de commitar.
**Release:** `chore: genai test tiers + markers + harness` — sem bump de versão (interno, sem superfície pública).

---

## #5 — honrar `seed`/`stop` no path transformers (v0.139.0)

**Meta:** `GenerationConfig.seed` e `.stop` passam a ter efeito real no
`TextGenerator` (hoje são dropados e ignorados — bug confirmado em
`schemas.py:186-195` + ausência de `set_seed`/`stop_strings` em `text.py`).

**Checkpoints**
- [ ] `TextGenerator._resolve_control(overrides, config) -> (seed, stop)` — pop de `overrides`, precedência override>config.
- [ ] `_generate_sync`/`_chat_sync`/`stream` aplicam `transformers.set_seed(seed)` e `generate(..., stop_strings=stop, tokenizer=self._tokenizer)`.
- [ ] docstring de `to_generate_kwargs` corrigida (reaplicados pelo generator, não "by the caller").
- [ ] paridade Ollama conferida (`ollama.py` `_build_options` mapeia `seed`+`stop`).
- **DoD (camada 1):** `_resolve_control` extrai/prioriza corretamente; `to_generate_kwargs` continua excluindo seed/stop (guard). **DoD (camada 2):** tiny-random — `stop=["\n"]` corta na 1ª quebra; mesmo `seed` + `do_sample=True` → duas saídas idênticas, seeds diferentes → diferentes.

**Deps:** depende do chore-infra (usa camada 2). Não desbloqueia outros.
**Passos código:** `genai/text.py` (novo método + 3 call sites), `genai/schemas.py` (docstring). Ollama alinhado se faltar.
**Extras novos:** nenhum.
**Docs:** `docs/recipes/genai-text.md` + `.en.md` — nota "seed/stop honrados no path local"; CHANGELOG `Fixed`.
**Risco/rollback:** `stop_strings` exige transformers ≥4.44 (já pinado). Se um modelo antigo não suportar, cai em `StoppingCriteria` manual. Rollback = reverter commit (isolado).
**Release:** `fix: honor GenerationConfig seed/stop on transformers path (v0.139.0)`.

---

## #8 — retry/backoff nas chamadas httpx (v0.140.0)

**Meta:** chamadas de rede do Ollama e do SearXNG ganham retry/backoff/
circuit-breaker + `X-Request-ID` reusando o `HTTPClient` do SDK, em vez de
`httpx.AsyncClient` cru.

**Checkpoint 0 (bloqueia design):**
- [ ] Ler `tempest_fastapi_sdk/utils` (`HTTPClient`) — confirmar API async, timeout por-request, e **suporte a streaming**. Se não streamar, escopo reduz: retry só em `generate`/`chat`/`embed`; `stream()` mantém httpx nu (documentado).

**Checkpoints**
- [ ] `OllamaGenerator`/`OllamaEmbedder` usam `HTTPClient` (default), `client=` injetável mantido.
- [ ] `SearxngBackend` idem.
- [ ] streaming NDJSON do Ollama (`ollama.py:328-361`) preservado.
- **DoD (camada 1):** com `stub_httpx` roteirado p/ N falhas→sucesso, cliente re-tenta N vezes; breaker abre após limite; header `X-Request-ID` propagado. **DoD (camada 2):** Ollama local — `generate` e `stream` funcionam ponta a ponta.

**Deps:** independe de #5. **Passos código:** `genai/ollama.py`, `genai/rag/search.py`.
**Extras novos:** nenhum (reuso interno). Conferir que `HTTPClient` não puxa dep fora de `[genai-ollama]`/`[genai-rag]` (ambos já têm httpx).
**Docs:** recipes ollama + rag — nota confiabilidade; CHANGELOG `Changed`.
**Risco/rollback:** regressão em streaming (maior risco). Mitigação: teste camada 2 obrigatório antes do merge. Rollback = reverter.
**Release:** `feat: retry/backoff on Ollama + SearXNG HTTP calls (v0.140.0)`.
