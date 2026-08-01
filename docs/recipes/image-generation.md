# Geração de imagem (local)

O SDK já gerava texto, entendia imagem (VLM), embedava, transcrevia e
sintetizava voz. Faltava **desenhar**. `ImageGenerator` fecha essa lacuna
rodando um modelo de difusão do HuggingFace no seu hardware — sem API paga,
sem sair da máquina.

```bash
uv add "tempest-fastapi-sdk[genai,genai-image]"
```

!!! info "Espelha o `TextGenerator`"
    Mesma resolução de device/precisão, mesmo load preguiçoso, mesmo
    `unload_if_idle`, mesmas palavras-chave de fixação do Hub
    (`revision=`/`local_files_only=`/`trust_remote_code=`). Quem já
    self-hospeda um LLM ganha imagem sem aprender uma segunda convenção.

!!! warning "O `[genai-image]` traz um limite superior"
    O `diffusers` declara `httpx<1.0.0` e `huggingface-hub<2.0`. Hoje nenhum
    dos dois morde (o httpx está na série 0.28), e por ser um extra opcional
    o limite só entra na resolução de quem instala. Ainda assim: se o seu
    serviço depende de httpx 1.x no futuro, esse extra é o lugar de olhar
    primeiro.

## O primeiro desenho

```python
from pathlib import Path

from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
images = await generator.generate("a lighthouse at dawn")
Path("lighthouse.png").write_bytes(images[0].data)
print(images[0].seed, images[0].width, images[0].height)
```

```text
418223901 512 512
```

Repare no que voltou: **não são bytes soltos**, é uma lista de
`GeneratedImage` com a **seed** junto. Difusão é determinística dada a seed,
então devolvê-la é a diferença entre "imagem boa, perdida pra sempre" e
"imagem boa, e aqui está como reproduzir". Quando você não passa seed, só o
gerador sabe qual foi sorteada — por isso ele conta.

## Configurar: turbo e completo querem coisas opostas

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

turbo = ImageGenerator("stabilityai/sdxl-turbo")
images = await turbo.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(steps=4, guidance_scale=0.0, seed=7),
)
```

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

full = ImageGenerator("stabilityai/stable-diffusion-xl-base-1.0")
images = await full.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(
        steps=30,
        guidance_scale=7.5,
        width=1024,
        height=1024,
        negative_prompt="blurry, watermark",
    ),
)
```

| Campo | Modelo turbo/destilado | Modelo completo |
| --- | --- | --- |
| `steps` | 1–8 | 20–50 |
| `guidance_scale` | `0.0` | 5–9 |
| `width`/`height` | o nativo do modelo | 1024 no SDXL |

!!! tip "Só o que você define é enviado"
    Campos não preenchidos caem no default do próprio modelo. Isso importa
    mais aqui que no texto: passar `steps=30` num modelo turbo desperdiça 26
    passos, e passar `guidance_scale=7.5` nele degrada a imagem.

## Reproduzir

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
first = await generator.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(seed=7, steps=4, guidance_scale=0.0),
)
again = await generator.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(seed=7, steps=4, guidance_scale=0.0),
)
print(first[0].data == again[0].data)
```

```text
True
```

Mesma seed, mesmo prompt, mesmo hardware → mesma imagem. Entre GPUs
diferentes o resultado pode divergir levemente (kernels e ordem de redução
não são idênticos), então trate a seed como reprodutibilidade *no seu*
host, não como hash universal.

## Redesenhar uma imagem existente

```python
from pathlib import Path

from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
edited = await generator.edit(
    "the same room, at night",
    "room.png",
    strength=0.6,
)
Path("room-night.png").write_bytes(edited[0].data)
```

`strength` diz o quanto sair da entrada: perto de `0.0` mantém a composição
quase intacta, `1.0` praticamente ignora a imagem original. A entrada aceita
caminho, `bytes`, `PIL.Image` ou array NumPy.

!!! check "O pipeline de edição não custa VRAM extra"
    Ele é montado com `AutoPipelineForImage2Image.from_pipe`, que
    **reaproveita** UNet, VAE e text encoders já carregados em vez de ler uma
    segunda cópia do disco. Um pipeline SDXL tem ~7 GB; carregá-lo duas vezes
    numa placa só é como um serviço estoura no primeiro request de edição.

## Servir por HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import ImageGenerator, make_genai_router

app = FastAPI()
app.include_router(
    make_genai_router(image_generator=ImageGenerator("stabilityai/sdxl-turbo")),
)
```

```bash
curl -X POST http://127.0.0.1:8000/api/genai/image \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a lighthouse at dawn", "config": {"steps": 4, "guidance_scale": 0.0}}' \
  --output lighthouse.png --dump-header -
```

```text
HTTP/1.1 200 OK
content-type: image/png
x-image-seed: 418223901
```

O corpo da resposta **é a imagem**, então a rota devolve só a primeira; a
seed vai no header `X-Image-Seed`. Quer um lote, use a classe direto — a
rota existe para o caso comum de uma imagem por request.

## Não deixar a GPU presa

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/sdxl-turbo",
    idle_unload_seconds=300.0,
)
```

Uma tarefa periódica chama `generator.unload_if_idle()` e a VRAM volta
depois de cinco minutos parada. O próximo request recarrega.

!!! note "Concorrência default é 1, de propósito"
    Diferente de um LLM, uma chamada de difusão já satura a GPU. Rodar duas
    em paralelo deixa as duas mais lentas e **dobra o pico de VRAM**. Suba
    `max_concurrent` só se você mediu que sobra placa.

## Fixar o modelo, como em todo o resto

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/sdxl-turbo",
    revision="f4b0486b498f84668e828044de1d0c8ba486e05b",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
```

As mesmas três palavras-chave dos outros loaders. Baixe antes de servir com
`tempest model pull` e veja [Pesos de modelos (ciclo no Hub)](model-weights.md)
— um pipeline de difusão pesa vários gigabytes, e pagá-lo dentro do primeiro
request é pior aqui que em qualquer outro lugar.

## Decisões de load: `pipeline_kwargs`

Algumas escolhas acontecem **na hora de carregar**, não na hora de desenhar —
e aí o escape hatch não serve, porque o custo já foi pago. Para essas, passe
`pipeline_kwargs`:

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/stable-diffusion-2-1",
    pipeline_kwargs={
        "safety_checker": None,
        "variant": "fp16",
        "use_safetensors": True,
    },
)
```

| Chave | Por que importa |
| --- | --- |
| `safety_checker: None` | Repositórios Stable Diffusion 1.x/2.x embutem um CLIP extra só para filtrar. Ele ocupa memória e às vezes devolve a imagem preta. |
| `variant: "fp16"` | Baixa os pesos em meia precisão — costuma cortar o download pela metade. |
| `use_safetensors: True` | Recusa checkpoint em pickle. |

!!! warning "Desligar o filtro é decisão sua, e ela tem licença"
    A licença do Stable Diffusion pede que resultados não filtrados não sejam
    expostos ao público. Desligue com consciência do seu caso de uso — o
    próprio `diffusers` avisa em runtime quando você faz isso.

As chaves de `pipeline_kwargs` são aplicadas **por último**, então elas
vencem o que o SDK calculou — é também assim que você sobrescreve o
`torch_dtype`.

## Trocar o scheduler (escape hatch)

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/stable-diffusion-xl-base-1.0")
pipeline = generator.pipeline
print(type(pipeline).__name__)
```

`.pipeline` devolve o objeto do `diffusers` (carregando na primeira vez).
Use-o para trocar o scheduler, anexar um LoRA ou ligar uma otimização de
memória que o SDK não embrulha — o SDK cobre o caminho comum e sai da frente
no resto.

## Recapitulando

- **`generate(prompt, config=...)`** desenha; devolve `GeneratedImage` com a
  **seed** que reproduz o resultado.
- **`ImageGenerationConfig`** tipa `steps`/`guidance_scale`/`width`/`height`/
  `seed`/`num_images`; turbo e completo pedem valores opostos.
- **`edit(prompt, image, strength=...)`** redesenha, reusando os componentes
  já carregados — sem VRAM extra.
- **`make_genai_router(image_generator=...)`** publica `POST /image`, com a
  seed no header.
- **`idle_unload_seconds`** devolve a VRAM quando ninguém desenha.
- **`revision=`/`local_files_only=`** fixam o modelo, iguais ao resto do SDK.

Onde continuar: [IA generativa self-hosted](genai.md) para texto, embeddings
e RAG, e [Pesos de modelos](model-weights.md) para o ciclo de vida do
download.
