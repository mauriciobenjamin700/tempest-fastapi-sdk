# Visão multimodal (VLM local)

Use para perguntar sobre uma imagem a um modelo de visão e linguagem rodando local.

Parte da receita [IA generativa self-hosted](../genai.md).

O `VisionTextGenerator` é o irmão multimodal do `TextGenerator`: carrega
um `AutoModelForVision2Seq` + `AutoProcessor` e gera texto condicionado a
imagens, no seu hardware. Paridade com o `OllamaGenerator`, que já aceita
`images`. Requer `[genai]` + `[genai-vlm]` (Pillow).

```python
import asyncio

from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")


async def main() -> None:
    """Run this example."""
    descricao = await gen.generate(
        "USER: <image>\nDescreva a imagem.\nASSISTANT:",
        images=["foto.jpg"],
    )


asyncio.run(main())
```

As imagens entram como caminho, `bytes`, `PIL.Image` ou `ndarray` NumPy
(mesma leniência do `ort-vision-sdk`). `generate`/`chat` são
image-opcionais — chamadas só-texto continuam funcionando (é um
`TextBackend`).

O `GenerationConfig` vale aqui campo por campo como no `TextGenerator`, e
os argumentos por chamada também — incluindo `stop_event`:

- `seed` usa um `generator` próprio da chamada na amostragem simples;
- `stop` (no config ou `stop=` por chamada, que vence o config) encerra a
  decodificação no token que completa a string de parada, lida contra o
  tokenizer do processor. A string de parada **fica** no texto devolvido,
  igual ao `TextGenerator`;
- os demais campos (`max_new_tokens`, `temperature`, `top_p`, `top_k`,
  `repetition_penalty`, `do_sample`) vão para o `model.generate`.

```python
import asyncio
import threading

from tempest_fastapi_sdk.genai import GenerationConfig, VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
config = GenerationConfig(max_new_tokens=128, do_sample=False, stop=["\n\n"])


async def main() -> None:
    """Run this example."""
    parar = threading.Event()
    legenda: str = await gen.generate(
        "USER: <image>\nDê uma legenda curta.\nASSISTANT:",
        images=["foto.jpg"],
        config=config,
        stop=["."],
        stop_event=parar,
    )
    print(legenda)


asyncio.run(main())
```

Antes, `stop` no config era descartado em silêncio (a geração passava da
string de parada), e `stop=` ou `stop_event=` por chamada faziam o
`model.generate` levantar `ValueError` listando o argumento entre os
`model_kwargs` não usados. `tests/genai/test_vision_text_config.py` percorre
`GenerationConfig.model_fields`: campo novo que o VLM nem aplica nem recusa
com erro claro derruba o teste.

!!! warning "Convenções de processor variam por família"
    Esta classe mira a interface comum `processor(text=..., images=...)`
    de LLaVA e Qwen2-VL. Outras famílias podem exigir um adaptador fino
    (token de placeholder de imagem, formato do chat template). Valide o
    modelo alvo antes de produção.
