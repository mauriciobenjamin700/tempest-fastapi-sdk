# Plano C — Estratégia de validação para o roadmap genai self-hosted

Documento transversal. Define **como provamos que cada item do roadmap
funciona**, já que quase tudo em `genai/` depende de pesos de modelo que não
rodam (ou não rodam bem) em CI. Sem este plano os DoDs dos itens #2–#14 ficam
ocos. Referência do roadmap: `memory/genai-selfhosted-roadmap.md`.

## Fato de partida (infra atual, verificado 2026-07-23)

- CI (`.github/workflows/ci.yml`) roda `uv sync --all-extras` → **torch +
  transformers + accelerate + faster-whisper + coqui-tts + bitsandbytes estão
  instalados** nos runners (Python 3.11/3.12/3.13, `ubuntu-latest`).
- Mas: **sem GPU** (CUDA indisponível), **sem pesos baixados** (nenhum cache
  HF), e `bitsandbytes` sem CUDA = quantização não exercitável.
- `make check` = `lint + fmt-check + type + test`. Sem gate numérico de
  cobertura (`--cov` reporta, não falha).
- `pytest` com `--strict-markers` + `--strict-config` → **todo marker novo tem
  de ser registrado** em `[tool.pytest.ini_options].markers` senão a suíte
  quebra.
- Testes genai atuais são **pure-logic**: resolvers de device/dtype, estado
  load/unload, serialização de config, parsing de respostas canônicas. Paths de
  modelo real levam `# pragma: no cover`.

**Conclusão:** mecânica (fiação/kwargs/parsing/serialização) É testável em CI.
Comportamento (o modelo realmente emitir tool-call, JSON válido, bom rerank)
NÃO é gate de CI — vira smoke opt-in com modelo minúsculo + checklist manual.

## Taxonomia de testes — 3 camadas

### Camada 1 — `unit` (pure-logic, roda sempre, é o gate)
- Sem pesos, sem rede. Fakes/mocks p/ tokenizer/model/httpx.
- Cobre: resolução de kwargs, precedência de config, serialização de
  tool-spec/schema, parsing de saída canônica, fusão RRF, cosine, chunking,
  mapeamento de opções Ollama, roteadores (via `TestClient` + backend fake).
- **É o único gate obrigatório** em `make check` / CI. Meta de cobertura das
  linhas novas: ≥90% (medida, não bloqueante ainda — ver "Gate de cobertura").

### Camada 2 — `model` (smoke com modelo minúsculo, opt-in)
- Marca `@pytest.mark.model`. Baixa 1 modelo minúsculo CPU e roda o caminho
  real ponta a ponta — prova **mecânica com pesos reais**, não qualidade.
- Desligado por default (`-m "not model and not gpu"`). Ligado via
  `make test-model` (baixa e cacheia em `~/.cache/huggingface`).
- Modelos minúsculos fixados (CPU, download pequeno):
  - causal LM mecânica: `hf-internal-testing/tiny-random-LlamaForCausalLM`
    (gera lixo, mas exercita `generate`/`stop_strings`/`streamer`).
  - chat template + tools **de verdade**: `Qwen/Qwen2.5-0.5B-Instruct`
    (~1 GB; tem chat template e suporte a `tools=`). Usado só quando a
    mecânica precisa de um template real (itens #2/#3).
  - embeddings: `hf-internal-testing/tiny-random-BertModel` (mecânica) /
    `sentence-transformers/all-MiniLM-L6-v2` (~90 MB, qualidade local).
  - reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~90 MB).
- Roda num **job CI separado, opcional, `continue-on-error`** (não bloqueia
  merge) — nightly ou label `run-model-tests`. Motivo: download flaky + minutos.

### Camada 3 — `gpu` / manual (comportamento + quantização + VLM/áudio)
- Marca `@pytest.mark.gpu` (pula sem CUDA via `torch.cuda.is_available()`).
- Nunca roda em CI. Rodada **localmente** na máquina do dev (WSL2) quando há
  GPU, ou substituída por **checklist de validação manual** colado no PR.
- Cobre o que exige julgamento de qualidade ou hardware: modelo instruct real
  emitindo tool-call correto, JSON schema-válido, rerank melhorando ordem,
  int4/int8, VLM com imagem, STT/TTS com áudio real.

## Infra a construir (chore, uma vez — antes da Fase 1)

Commit único `chore: genai test tiers + markers + manual-validation harness`:

1. **Registrar markers** em `pyproject.toml`:
   ```toml
   [tool.pytest.ini_options]
   markers = [
       "model: needs real model weights (opt-in, downloads on first run)",
       "gpu: needs CUDA (skipped without a GPU)",
   ]
   addopts = [..., "-m", "not model and not gpu"]  # default exclui camadas 2/3
   ```
2. **Alvos Makefile**:
   ```make
   test-model: ## Run opt-in model smoke tests (downloads tiny weights)
   	uv run pytest -m model
   test-gpu:   ## Run GPU tests (needs CUDA)
   	uv run pytest -m gpu
   ```
3. **Fixtures compartilhadas** em `tests/genai/conftest.py`:
   - `fake_text_backend` — implementa `TextBackend` com saídas roteiráveis
     (fila de respostas, tool-calls canônicos) p/ testar pipeline/router/tools
     sem pesos.
   - `tiny_causal_lm` (marca `model`) — carrega o tiny-random e cacheia por
     sessão.
   - `stub_httpx` — cliente httpx fake (respostas NDJSON scriptadas) p/ Ollama.
4. **Harness de eval RAG** `tests/genai/_eval/` — corpus fixo pequeno
   (~30 docs) + queries rotuladas + `recall_at_k()`/`mrr()` puros. Alimenta os
   DoDs de #4/#6/#13 com número reprodutível (embedder fake determinístico ou
   `all-MiniLM` sob marca `model`).
5. **Template de checklist manual** `planning/genai/manual-validation.md` —
   uma seção por item que precisa de camada 3, com comandos exatos + saída
   esperada, resultado colado no PR.

## Mapeamento item → camada exigida

| Item | Camada 1 (gate) | Camada 2 (smoke) | Camada 3 (manual/gpu) |
|---|---|---|---|
| #5 seed/stop | `_resolve_control`, kwargs montados | tiny-random: stop corta, seed repete | — |
| #8 retry httpx | retry/breaker com stub_httpx | Ollama local NDJSON | — |
| #2 tools transformers | tool-spec serializa, parser de tool-call | Qwen-0.5B emite call parseável | instruct 7B: call correto |
| #3 structured | schema→constraint montado, reparse valida | Qwen-0.5B: JSON schema-válido | 7B: campos corretos |
| #10 VLM | processor monta inputs de imagem | tiny-VLM: forward roda | LLaVA real: descreve imagem |
| #4 reranker | ordena por score dado logits fake | cross-encoder MiniLM ordena | qualidade vs baseline |
| #6 hybrid | RRF funde ranks corretamente | recall@5 no corpus fixo | — |
| #13 onnx embed | pooling correto vs vetor conhecido | MiniLM-onnx bate MiniLM-torch | — |
| #9 cache geração | hit/miss/invalidate | — | — |
| #11 token count | conta tokens vs esperado, trunca | tokenizer real bate contagem | — |
| #12 observabilidade | métricas incrementam | — | latência real plausível |
| #7 vision router | endpoints via detector fake | — | modelo ONNX real |
| #14 moderação | bloqueia/passa por rótulo fake | classificador real rotula PT-BR | qualidade PT-BR |

## Gate de cobertura (decisão)

Adotar `--cov-fail-under` só sobre **arquivos novos/alterados** seria ideal mas
`coverage` não faz isso nativamente. Decisão pragmática: manter cobertura como
relatório; cada PR de item declara no corpo a cobertura das linhas novas
(Camada 1) e ≥90% é a meta. Reavaliar gate global depois da Fase 1.

## Definição de "pronto" reutilizável (todo item herda)

Um item só fecha quando:
1. Camada 1 verde e cobrindo as linhas novas (≥90% meta).
2. Se a tabela marca Camada 2: teste `@model` passa localmente (saída colada no PR).
3. Se marca Camada 3: seção de checklist manual preenchida no PR, ou justificado
   "sem GPU/modelo — não verificado" explicitamente (nunca silencioso).
4. `make check` + `mkdocs build --strict` + `make smoke` verdes.
5. Docs bilíngue (`docs/<page>.md` + `.en.md`) + nav + API ref atualizados.
6. Re-export duplo (`as` + `__all__`) p/ todo símbolo público novo.
7. CHANGELOG + version bump em `pyproject.toml` e `__init__.py`.

## Ordem de execução

`chore` de infra (markers + fixtures + eval harness + checklist template) vem
**antes de #5**, porque #5 já quer um smoke `@model` (stop corta / seed repete).
Depois segue a ordem do roadmap.
