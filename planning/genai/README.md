# Planejamento — roadmap genai self-hosted

Planos de execução das integrações IA. Foco **estrito self-hosted** — item
"cliente OpenAI-compatible" descartado de propósito. Fonte de verdade do
escopo: `../../CLAUDE.md` (lista "covers") + `memory/genai-selfhosted-roadmap.md`.

## Documentos

- [`validation-strategy.md`](validation-strategy.md) — **plano C**: taxonomia
  de testes (unit/model/gpu), infra de markers, harness fake-backend, eval RAG
  recall@k, definição de pronto reutilizável. **Ler primeiro.**
- [`phase-0.md`](phase-0.md) — chore-infra de testes, #5 seed/stop, #8 retry httpx.
- [`phase-1.md`](phase-1.md) — #2 tools, #3 structured output, #10 VLM.
- [`phase-2.md`](phase-2.md) — #4 reranker, #6 hybrid search, #13 ONNX embeddings.
- [`phase-3.md`](phase-3.md) — #9 cache geração, #11 token/contexto, #12 métricas, #7 vision router, #14 moderação.
- `manual-validation.md` — (criado no chore-infra) checklist de validação
  camada 3, resultado colado no PR de cada item.

## Sequência

```
chore-infra → #5 → #8 → #2 → #3 → #10 → #4 → #6 → #13 → #9 → #11 → #12 → #7 → #14
   (C)        v0.139  v0.140  v0.141 ... v0.151
```

Cada item = branch → implementação → testes → docs bilíngue → CHANGELOG +
bump → commit. **Sem push até liberação explícita.**

## Template de spec (todo item segue)

Meta · Checkpoints (com DoD mensurável) · Deps entre itens · Passos código ·
Plano de teste (camadas 1/2/3) · Extras novos · Docs (página + `.en.md` + nav +
API ref) · Risco/rollback · Release.
