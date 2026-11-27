---
description: "Use para gerar efeitos sonoros e música (MusicGen) com `AudioGenerator` e servir o resultado por HTTP em `/audio`."
---

# Efeitos sonoros e música

Use para gerar efeitos sonoros e música (MusicGen) e servir o resultado por HTTP em `/audio`.

Parte da receita [IA generativa self-hosted](../genai.md).

Gerar um trovão, passos na folha seca ou uma trilha lo-fi no seu hardware,
sem API externa: `AudioGenerator` carrega um pipeline de difusão de áudio do
HuggingFace e segue as mesmas convenções do `ImageGenerator` (carga
preguiçosa numa thread, descarga por ociosidade, uma geração por vez, seed
devolvida com cada clipe).

Requer os extras `[genai]` (torch + transformers) e `[genai-sound]`
(`diffusers`):

```bash
uv add "tempest-fastapi-sdk[genai,genai-sound]"
```

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, AudioGenerator

efeitos = AudioGenerator("cvssp/audioldm-s-full-v2")


async def main() -> None:
    """Gera 10 s de tempestade e grava em disco."""
    clipes = await efeitos.generate(
        "thunderstorm with heavy rain on a tin roof",
        config=AudioGenerationConfig(seconds=10.0, steps=50, seed=0),
    )
    clipe = clipes[0]
    Path("tempestade.wav").write_bytes(clipe.data)
    print(clipe.sample_rate, clipe.seconds, clipe.seed)


asyncio.run(main())
```

```text
16000 10.0 0
```

Cada `GeneratedAudio` é um WAV PCM de 16 bits com `sample_rate`,
`channels`, `seconds` e a `seed`. Sem `seed` no config, uma é sorteada e
devolvida, então todo clipe é reproduzível no mesmo hardware e precisão:
medido na RTX 4070 Ti SUPER em bf16, a mesma seed gerou os mesmos bytes, e
`num_waveforms=2` gerou dois clipes diferentes numa chamada (reproduzir o
segundo pede a mesma seed, o mesmo `num_waveforms` e a posição dele).

## Que modelo usar

Medido em 2026-10-31 numa RTX 4070 Ti SUPER (bf16, 10 s, 50 passos, modelo
já carregado):

| Modelo | Para | 10 s com 50 passos | Sem config (default do modelo) | Licença |
| --- | --- | --- | --- | --- |
| `cvssp/audioldm-s-full-v2` | efeitos sonoros | ~1,1 s | 5,12 s de áudio, 10 passos | `cc-by-nc-sa-4.0` |
| `ucsd-reach/musicldm` | música | ~1,1 s | 10,24 s de áudio, 200 passos | `cc-by-nc-sa-4.0` |

O pico de VRAM ficou abaixo de 1,1 GiB nos dois.

!!! warning "Os dois modelos são não comerciais"
    `cc-by-nc-sa-4.0` proíbe uso comercial. Confira o model card antes de
    colocar em produto.

!!! danger "Onde cada modelo funciona"
    O `diffusers` mantém `AudioLDMPipeline` e `MusicLDMPipeline` em
    `pipelines/deprecated` ("will not receive bug fixes or feature updates
    after Diffusers version 0.33.1"): o código está congelado. Medido em duas
    resoluções:

    | Ambiente | AudioLDM | MusicLDM | AudioLDM2 |
    | --- | --- | --- | --- |
    | `transformers` 4.57.6 + `diffusers` 0.39.0 (com `transformers<5` fixado) | gera | gera | recusado |
    | `transformers` 5.22.1 + `diffusers` 0.42.1 (`[genai,genai-sound]` sozinho) | gera | recusado | recusado |

    "Recusado" quer dizer que `load()` levanta `ValueError` com o motivo,
    em vez de o primeiro `generate` estourar um `AttributeError` lá dentro:

    - **AudioLDM2** carrega mas falha na primeira chamada com
      `'GPT2Model' object has no attribute '_update_model_kwargs_for_generation'`;
      a correção upstream ([diffusers#13111](https://github.com/huggingface/diffusers/pull/13111))
      foi fechada sem merge.
    - **MusicLDM** com `transformers` 5.22.1: `ClapModel.get_text_features`
      devolve um `BaseModelOutputWithPooling` em vez de um tensor, e o
      pipeline quebra com `'BaseModelOutputWithPooling' object has no
      attribute 'to'`. A carga roda essa chamada uma vez para decidir, sem
      adivinhar versão, e libera a VRAM ao recusar: medido, ficam 8 MiB
      alocados com a exceção ainda viva.
    - **Stable Audio** usa `audio_end_in_s` no lugar de `audio_length_in_s`
      e também é recusado.

## Música com MusicGen

`MusicGenerator` roda o MusicGen da Meta pelo `transformers`, sem difusão e
sem `diffusers`: basta o extra `[genai]`. Mesmo `AudioGenerationConfig`,
mesmo `GeneratedAudio`, mesma rota `/audio`.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, MusicGenerator

musica = MusicGenerator("facebook/musicgen-stereo-small")


async def main() -> None:
    """Gera 5 s de rock em estéreo e grava em disco."""
    clipes = await musica.generate(
        "upbeat rock riff",
        config=AudioGenerationConfig(seconds=5.0, seed=0),
    )
    clipe = clipes[0]
    Path("rock.wav").write_bytes(clipe.data)
    print(clipe.channels, clipe.seconds, clipe.sample_rate)


asyncio.run(main())
```

```text
2 4.94 32000
```

O que muda em relação à difusão, tudo medido numa RTX 4070 Ti SUPER com
`transformers` 4.57.6 e 5.22.1 (os dois se comportaram igual):

- **A duração é um orçamento de tokens.** São 50 frames por segundo, e o
  clipe sai 60 ms mais curto que o pedido (5 s viram 4,94 s).
  `GeneratedAudio.seconds` traz a duração real. Sem `seconds`, vale o
  default do checkpoint: 29,92 s no `musicgen-small` e no
  `musicgen-stereo-small`.
- **Há um teto: 40,96 s.** O decoder tem 2048 posições; 2048 tokens geraram
  40,9 s e 2049 dispararam um device-side assert do CUDA que **envenena o
  processo**: toda chamada CUDA seguinte falhou. Por isso `generate` recusa
  com `422` (`details={"field": "seconds", "limit": 40.96}`) antes de chamar
  o modelo, e `max_seconds` expõe o teto.
- **`steps` e `negative_prompt` não existem aqui** e respondem `422` em vez
  de serem ignorados. `guidance_scale` e `num_waveforms` funcionam.
- **Estéreo vem do checkpoint**: `musicgen-stereo-small` devolve 2 canais.
- **Tempo**: 10 s de áudio levaram ~9,2 s em bf16, ~9,3 s em fp16 e ~10 s
  em fp32 (`musicgen-small`, um prompt); 30 s, ~31 a 33 s. Pico de VRAM de
  1,3 GiB (5 s) a 2,5 GiB (35 s), para dois clipes por chamada.
- **A seed é global.** O `generate` do `transformers` não recebe um gerador,
  então a seed vai em `torch.manual_seed`, que semeia o processo inteiro. A
  mesma seed repetiu os mesmos bytes.

!!! warning "Pesos não comerciais"
    Os checkpoints do MusicGen são `cc-by-nc-4.0`.

### Seguindo uma melodia: `musicgen-melody`

Com um checkpoint `musicgen-melody`, `generate(melody=...)` recebe um clipe de
referência (caminho ou bytes) e a música segue a melodia dele. O
`MusicGenerator` detecta o tipo do checkpoint na carga (`accepts_melody`),
decodifica o clipe com o `load_audio` (WAV 16 bits não precisa de nada; outros
formatos pedem o PyAV do `[genai-audio]`) e converte as features para a
precisão do modelo — em bf16, sem isso, o `transformers` quebra com
`mat1 and mat2 must have the same dtype`.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.genai import AudioGenerationConfig, MusicGenerator

melodia = MusicGenerator("facebook/musicgen-melody")


async def main() -> None:
    """Refaz o rock.wav como violão acústico."""
    clipes = await melodia.generate(
        "calm acoustic guitar version",
        config=AudioGenerationConfig(seconds=10.0, seed=0),
        melody=Path("rock.wav"),
    )
    Path("violao.wav").write_bytes(clipes[0].data)


asyncio.run(main())
```

Medido na RTX 4070 Ti SUPER, em bf16: 10 s levaram 10,8 s com o modelo quente
(pico de 3,3 GiB; 30 s, 4,3 GiB) e a mesma seed repetiu o resultado. Melodia
num checkpoint sem suporte (o `musicgen-small`) responde `422` com
`details={"field": "melody"}`. Os pesos do `musicgen-melody` também são
`cc-by-nc-4.0`.

## Servindo por HTTP: `/audio`

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import AudioGenerator, make_genai_router

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_genai_router(audio_generator=AudioGenerator("cvssp/audioldm-s-full-v2")),
)
```

`audio_generator` aceita qualquer `SupportsAudioGeneration`: um `AudioGenerator` ou um `MusicGenerator`.
Com `MusicGenerator("facebook/musicgen-small")`, `{"seconds": 4.0, "seed": 7}` respondeu `200` com 126080 frames a 32 kHz, e `{"steps": 5}`, `422`.

`POST /api/genai/audio` com
`{"prompt": "dog barking in the distance", "config": {"seconds": 4.0, "seed": 42}}`
respondeu `200`, `audio/wav` de 64000 frames a 16 kHz, com
`X-Audio-Seed: 42`. A rota gera um clipe só (`num_waveforms` acima de 1 é
`422`) e aplica os limites `max_audio_seconds` (30) e `max_audio_steps`
(200, o default do MusicLDM) do `GenAIRequestLimits`, respondendo `422`
com `details={"field": ..., "limit": ...}`.
