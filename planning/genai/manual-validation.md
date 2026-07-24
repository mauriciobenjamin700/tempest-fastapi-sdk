# Validação camada-3 (modelo/GPU real)

Automatizada como **testes `@pytest.mark.gpu`** em
`tests/genai/test_gpu_validation.py` — rode com `make test-gpu` (ou
`uv run --all-extras pytest -m gpu`) numa máquina com CUDA. Deselecionados por
default. Este arquivo registra os resultados da última execução.

## Última execução — 2026-07-24 · RTX 4070 Ti SUPER (16 GB) · CUDA · transformers 5.13

| Item | Modelo | Resultado |
|---|---|---|
| #4 reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ✅ rankeia o chunk PIX em 1º |
| #14 moderação PT-BR | `textdetox/xlmr-large-toxicity-classifier` | ✅ tóxico PT-BR flagueado, limpo não |
| #2 tool calling | `Qwen/Qwen2.5-3B-Instruct` | ✅ emite `get_weather` parseável |
| #3 structured **constrained** | `Qwen/Qwen2.5-3B-Instruct` | ✅ JSON schema-válido no **tf5** (fix lmfe core) |
| #13 ONNX vs torch | `sentence-transformers/all-MiniLM-L6-v2` | ✅ cosseno ≥ 0.999 |
| #10 VLM | `Qwen/Qwen2-VL-2B-Instruct` | ✅ carrega + gera no **tf5** (`AutoModelForImageTextToText` + torchvision) |

Comando: `uv run --all-extras pytest -m gpu tests/genai/test_gpu_validation.py`.

## Dívida transformers-5.x — RESOLVIDA (v0.155.0)

- **structured constrained:** o `build_prefix_allowed_tokens_fn` deixou de
  importar `lmformatenforcer.integrations.transformers` (cujo import de
  `PreTrainedTokenizerBase` quebrou na tf5) e passou a montar o adapter a
  partir do **core** do lmfe (`JsonSchemaParser`/`TokenEnforcer`/
  `TokenEnforcerTokenizerData`). Funciona no tf4 e tf5.
- **VLM:** `VisionTextGenerator.load` usa `AutoModelForImageTextToText`
  (fallback `AutoModelForVision2Seq`); `torchvision` (exigido pelos processors
  VLM modernos) entrou no extra `[genai-vlm]`.

## Caveat remanescente (não-blocker)

- **Correção multimodal por-modelo (VLM):** o pipeline carrega e gera no tf5,
  mas a acurácia da resposta depende do wiring do processor de cada família
  (Qwen2-VL/LLaVA divergem em placeholders de imagem). O teste `@gpu` afirma
  que o pipeline produz texto, não uma resposta específica. Ajuste o wiring do
  processor pro seu modelo alvo antes de produção.

## Como reproduzir

```bash
uv run --all-extras pytest -m gpu -s tests/genai/test_gpu_validation.py
```

Baixa os pesos na primeira execução (cacheados em `~/.cache/huggingface`).
