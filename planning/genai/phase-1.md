# Fase 1 — Geração local (TextGenerator / backend)

Todos herdam a "Definição de pronto" do plano C. Ordem: #2 → #3 → #10.

---

## #2 — tool calling no path transformers (v0.141.0)

**Meta:** `TextGenerator` implementa `chat_with_tools`, fechando a assimetria
onde `AIChatPipeline._run_tool_loop` exige `chat_with_tools` mas só
`OllamaGenerator` o tem — hoje pipeline com backend transformers não roda tools.

**Checkpoints**
- [ ] `TextGenerator.chat_with_tools(messages, tools, *, config, **kw)` usando `tokenizer.apply_chat_template(messages, tools=[...], add_generation_prompt=True)` (transformers ≥4.44).
- [ ] Parser de tool-call na saída → mesma forma que `OllamaGenerator.chat_with_tools` retorna (assinatura idêntica no protocolo).
- [ ] `TextBackend` Protocol (`text.py:32-73`) ganha `chat_with_tools` (opcional via `hasattr` no pipeline, ou método que levanta se não suportado).
- [ ] `Tool.to_spec()` (`pipeline.py:76-90`) reusado — sem formato novo.
- **DoD (camada 1):** dado saída canônica de tool-call, parser extrai `name`+`arguments` corretos; template recebe `tools=`. **DoD (camada 2):** Qwen2.5-0.5B-Instruct + tool `get_weather` → "clima em SP" produz tool-call **parseável** (mecânica, não acurácia). **DoD (camada 3, manual):** instruct 7B escolhe a tool certa em 3/3 prompts do checklist.

**Deps:** chore-infra. Desbloqueia tools no pipeline p/ backend local; pré-condição natural de #3 (structured reusa o loop de parsing).
**Passos código:** `genai/text.py` (novo método + helper de parse), `genai/__init__.py` (nada novo a exportar — método), possivelmente relaxar guarda em `pipeline.py`.
**Extras novos:** nenhum (`[genai]`).
**Docs:** `docs/recipes/genai-tools.md` + `.en.md` (novo) + nav + API ref; CHANGELOG `Added`.
**Risco/rollback:** parsing de tool-call varia por família (Qwen/Llama/Hermes usam formatos diferentes). Mitigação: começar com o formato do chat template (o próprio tokenizer emite marcadores), testar ≥2 famílias na camada 3. Rollback isolado.
**Release:** `feat: tool calling on transformers TextGenerator (v0.141.0)`.

---

## #3 — structured output / JSON-schema (v0.142.0)

**Meta:** geração restrita a um schema Pydantic — `response_schema` no
`GenerationConfig`; transformers via `lm-format-enforcer` (LogitsProcessor),
Ollama via `format=<json_schema>`. Retorna instância validada.

**Checkpoints**
- [ ] `GenerationConfig.response_schema: type[BaseModel] | None` (não serializa p/ `to_generate_kwargs`; tratado à parte, como seed/stop).
- [ ] `TextGenerator`: quando setado, monta `lm-format-enforcer` LogitsProcessor a partir do JSON schema do modelo Pydantic e injeta em `generate`.
- [ ] `OllamaGenerator`: passa `format=schema.model_json_schema()` no payload.
- [ ] Helper `parse_structured(text, schema) -> BaseModel` (reparse + validação, levanta `AppException` em falha).
- [ ] `make_genai_router` `/generate` aceita schema opcional (fora do escopo se complicar — decidir no checkpoint).
- **DoD (camada 1):** given schema + JSON canônico válido/inválido, `parse_structured` valida/rejeita; LogitsProcessor montado a partir do schema. **DoD (camada 2):** Qwen-0.5B + schema simples → saída **schema-válida** em 10/10 runs (validade estrutural, não valores). **DoD (camada 3):** 7B preenche campos plausíveis no checklist.

**Deps:** #2 (reusa infra de saída estruturada / loop). **Passos código:** `genai/schemas.py`, `genai/text.py`, `genai/ollama.py`, novo `genai/structured.py` (helper + montagem do enforcer).
**Extras novos:** `[genai-structured]` = `lm-format-enforcer>=0.10.0` (leve, não puxa stack extra além de transformers). Registrar no `pyproject` + mypy `ignore_missing_imports` + `all`? (não — heavy-opt-in, fora de `all`, como os outros `genai-*`).
**Docs:** `docs/recipes/genai-structured.md` + `.en.md` + nav + API ref; CHANGELOG `Added`.
**Risco/rollback:** `lm-format-enforcer` acopla à versão do transformers/tokenizer. Mitigação: pin conservador + teste camada 2. Fallback sem o extra = reparse best-effort (gera, tenta validar, levanta se inválido) — documentado.
**Release:** `feat: schema-constrained structured output (v0.142.0)`.

---

## #10 — VLM / multimodal no transformers (v0.143.0)

**Meta:** `TextGenerator` (ou novo `VisionTextGenerator`) aceita imagens como
input, via `AutoModelForVision2Seq` + `AutoProcessor` (LLaVA/Qwen-VL). Paridade
com o Ollama, que já aceita `images` base64.

**Checkpoints**
- [ ] Decidir: estender `TextGenerator` (flag multimodal) vs classe nova `VisionTextGenerator`. **Recomendação:** classe nova — evita inchar o caminho texto-puro e mantém `AutoModelForCausalLM` intacto.
- [ ] Aceita imagem como path/bytes/PIL/ndarray (mesmo leque do `ort-vision-sdk`).
- [ ] `chat`/`generate` montam `processor(text=..., images=...)` → `pixel_values`.
- [ ] Implementa `TextBackend` (texto-só ainda funciona sem imagem).
- **DoD (camada 1):** processor recebe imagens e monta inputs (com processor fake/tiny); aceitação dos tipos de entrada. **DoD (camada 2):** tiny-VLM (`hf-internal-testing/tiny-random-*Vision2Seq`) roda `forward` sem erro. **DoD (camada 3):** LLaVA/Qwen-VL real descreve uma imagem do checklist plausivelmente.

**Deps:** independe de #2/#3 (caminho separado). **Passos código:** novo `genai/vision_text.py`, `genai/__init__.py` (re-export duplo), `make_genai_router` opção multimodal (opcional).
**Extras novos:** reusa `[genai]` + Pillow (adicionar `pillow>=10.0.0` a `[genai]`? conferir se transformers já traz). Talvez `[genai-vlm]` se pesar.
**Docs:** `docs/recipes/genai-vlm.md` + `.en.md` + nav + API ref; CHANGELOG `Added`.
**Risco/rollback:** processors variam muito por modelo (chat template multimodal ainda instável no ecossistema). Mitigação: suportar 1-2 famílias documentadas (Qwen2-VL, LLaVA) explicitamente, não prometer genérico. Rollback = classe isolada, remover export.
**Release:** `feat: local VLM multimodal generation (v0.143.0)`.
