# Fase 2 — Qualidade RAG (Retriever / RAG)

Todos herdam a "Definição de pronto" do plano C. Estes três mexem no mesmo
`Retriever`/RAG — coordenar assinatura. Ordem: #4 → #6 → #13.
Todos usam o harness de eval `tests/genai/_eval/` (corpus fixo + recall@k/MRR).

---

## #4 — reranker cross-encoder (v0.144.0)

**Meta:** `Reranker` local re-ordena candidatos do retriever por relevância
query-doc via cross-encoder (`AutoModelForSequenceClassification`), encaixado
entre `search` e `build_context`.

**Checkpoints**
- [ ] `Reranker(model_id, ...)` com `rerank(query, chunks, top_k) -> list[Chunk]` (lazy load, to_thread, idle unload — padrão dos outros).
- [ ] `Retriever.retrieve(..., reranker=None)` ou `Retriever(reranker=...)` — decidir injeção (recomendo no construtor, opcional).
- [ ] `Chunk.score` reusado p/ o score do reranker.
- **DoD (camada 1):** dado logits fake, `rerank` ordena desc por score e corta em `top_k`. **DoD (camada 2):** `cross-encoder/ms-marco-MiniLM-L-6-v2` roda e ordena. **DoD (eval):** no corpus fixo, rerank sobre top-20 denso eleva **MRR@10 em ≥15% relativo** vs denso puro. Número no PR.

**Deps:** chore-infra + eval harness. **Passos código:** novo `genai/rag/rerank.py`, `genai/rag/retriever.py` (injeção), `genai/rag/__init__.py` (re-export duplo).
**Extras novos:** nenhum (`[genai]` — transformers já traz `AutoModelForSequenceClassification`). ONNX rerank opcional fica p/ depois.
**Docs:** `docs/recipes/genai-rag.md` (+`.en.md`) — seção rerank; API ref; CHANGELOG `Added`.
**Risco/rollback:** cross-encoder é mais uma carga de modelo (memória). Mitigação: opt-in, idle unload, `ModelRegistry`. Rollback = tornar `reranker=None` no-op.
**Release:** `feat: cross-encoder reranker for RAG (v0.144.0)`.

---

## #6 — hybrid search BM25 + denso (RRF) (v0.145.0)

**Meta:** recuperação híbrida — funde ranking esparso (BM25) e denso (vetor)
via Reciprocal Rank Fusion, subindo recall em nomes próprios / termos exatos
onde o denso falha.

**Checkpoints**
- [ ] `bm25_search(query, docs) -> ranks` sobre `rank-bm25` (puro Python).
- [ ] `reciprocal_rank_fusion(rankings, k=60) -> ordem fundida` (pura, testável).
- [ ] `HybridRetriever` ou `Retriever(hybrid=True)` — decidir. Recomendo `HybridRetriever` compondo o denso + índice BM25 (mantém `Retriever` simples).
- [ ] Índice BM25 construído no `index()` junto do vetor.
- **DoD (camada 1):** RRF funde rankings conhecidos na ordem esperada; BM25 rankeia por termo exato. **DoD (eval):** em 20 queries de nome-próprio do corpus, híbrido recupera doc-alvo no **top-5 em ≥18/20**; baseline denso ≤12/20. Números no PR.

**Deps:** convive com #4 (rerank aplica-se DEPOIS da fusão — ordem: híbrido → rerank → context). Documentar a pipeline combinada.
**Passos código:** novo `genai/rag/hybrid.py` + `genai/rag/fusion.py`, `genai/rag/__init__.py`.
**Extras novos:** `rank-bm25>=0.2.2` adicionado a `[genai-rag]` (minúsculo, puro Python). mypy override se preciso.
**Docs:** `docs/recipes/genai-rag.md` (+`.en.md`) — seção hybrid + diagrama da pipeline; API ref; CHANGELOG `Added`.
**Risco/rollback:** BM25 in-memory não escala a corpus gigante (aceitável — RAG do SDK é de escopo médio). Documentar limite. Rollback = usar só denso.
**Release:** `feat: hybrid BM25+dense retrieval with RRF (v0.145.0)`.

---

## #13 — embeddings ONNX (v0.146.0)

**Meta:** `OnnxEmbedder` — embeddings texto→vetor via ONNX Runtime, **sem
torch**, com pooling correto. Alternativa CPU-barata ao `Embedder`
(transformers) para serviços que não querem a stack pesada.

**Checkpoints**
- [ ] `OnnxEmbedder(model_path, tokenizer, *, pooling="mean", normalize=...)` implementando `SupportsEmbed` (mesmo protocolo do `Embedder`, plugável no `Retriever`).
- [ ] Pooling correto (mean com attention mask, não média ingênua) + normalize opcional.
- [ ] Aceita modelo ONNX local (exportado) ou baixado; tokenizer via `tokenizers`.
- [ ] Reusa `EmbeddingCache` (mesma interface do `Embedder`).
- **DoD (camada 1):** pooling bate vetor conhecido (mask aplicada corretamente) com sessão ONNX fake/minúscula. **DoD (camada 2):** MiniLM exportado p/ ONNX vs `all-MiniLM-L6-v2` torch — **cosine ≥0.999** em 20 frases. Número no PR.

**Deps:** encaixa no `Retriever` como qualquer `SupportsEmbed` — sem mudar #4/#6.
**Passos código:** novo `genai/onnx_embed.py`, `genai/__init__.py` (re-export duplo).
**Extras novos:** `[genai-onnx]` = `onnxruntime>=1.18.0` + `tokenizers>=0.20.0`. (onnxruntime já é dep transitiva do `ort-vision-sdk`, mas declarar explícito.) mypy overrides.
**Docs:** `docs/recipes/genai-embeddings.md` (+`.en.md`) — seção ONNX + como exportar; API ref; CHANGELOG `Added`.
**Risco/rollback:** pooling errado dá embedding silenciosamente ruim (risco alto, invisível). Mitigação: o DoD de cosine≥0.999 vs torch é exatamente o guard. Rollback = manter só `Embedder`.
**Release:** `feat: ONNX text embeddings backend (v0.146.0)`.
