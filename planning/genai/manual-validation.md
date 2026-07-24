# Validação camada-3 (modelo/GPU real)

Agora automatizada como **testes `@pytest.mark.gpu`** em
`tests/genai/test_gpu_validation.py` — rode com `make test-gpu` (ou
`uv run --all-extras pytest -m gpu`) numa máquina com CUDA. Deselecionados por
default. Este arquivo registra os resultados da última execução.

## Última execução — 2026-07-24 · RTX 4070 Ti SUPER (16 GB) · CUDA · torch 2.13

| Item | Modelo | Resultado |
|---|---|---|
| #4 reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ✅ rankeia o chunk PIX em 1º |
| #14 moderação PT-BR | `textdetox/xlmr-large-toxicity-classifier` | ✅ tóxico PT-BR flagueado, limpo não |
| #2 tool calling | `Qwen/Qwen2.5-3B-Instruct` | ✅ emite `get_weather` parseável |
| #3 structured (best-effort) | `Qwen/Qwen2.5-3B-Instruct` | ✅ JSON válido → `Person(age:int)` |
| #13 ONNX vs torch | `sentence-transformers/all-MiniLM-L6-v2` (onnx pré-exportado) | ✅ cosseno ≥ 0.999 |
| #10 VLM | `Qwen/Qwen2-VL-2B-Instruct` | ⏭️ SKIP — ver caveat abaixo |

Comando: `uv run --all-extras pytest -m gpu tests/genai/test_gpu_validation.py`.

## Caveats conhecidos

- **#10 VLM (Qwen2-VL):** `transformers` 5.x levanta
  `Qwen2VLVideoProcessor requires the Torchvision library` no
  `AutoProcessor.from_pretrained`, mesmo com `torchvision` instalado
  (checagem `is_torchvision_available` estrita/quebrada na 5.x). O
  `VisionTextGenerator` em si está correto (camada-1 valida normalização de
  imagem/device); o teste dá `skip` com a razão quando o processor não carrega.
  Revalidar quando transformers corrigir, ou com um VLM cujo processor não use
  o video-processor (ex. LLaVA image-only).
- **#3 structured constrained (transformers):** `lm-format-enforcer` 0.11.3 é
  incompatível com transformers 5.x (import movido). Validado o caminho
  `constrained=False` (best-effort) + a rota Ollama `format=` continua a
  recomendada. `constrained=True` levanta erro claro no skew.

## Como reproduzir

```bash
uv run --all-extras pytest -m gpu -s tests/genai/test_gpu_validation.py
```

Baixa os pesos na primeira execução (cacheados em `~/.cache/huggingface`).
Cada classe roda independente — `pytest -m gpu ...::TestRerankerQuality` etc.
