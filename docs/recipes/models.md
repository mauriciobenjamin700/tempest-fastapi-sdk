# Escolhendo o modelo

Todo objeto de IA do SDK recebe um **id de modelo em string**:

```python
from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct")
```

Isso funciona, mas erra feio: um typo (`Qwen2.5-7b-Instruct`) só aparece
quando o download devolve 404 — às vezes minutos depois, no meio de outra
coisa. E, pior, a pergunta que importa não é *como escrever o id*, é
**qual modelo escolher**.

Esta página responde as duas: um enum por tarefa e uma tabela de casos de
uso.

## O enum

```python
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)
```

`TextModel` é um `StrEnum`: cada membro **é** a string do id, então ele
vai direto em qualquer lugar que aceita `str` — construtor, settings,
JSON, query param.

```python
from tempest_fastapi_sdk.genai import TextModel

assert TextModel.QWEN2_5_7B_INSTRUCT == "Qwen/Qwen2.5-7B-Instruct"
print(f"carregando {TextModel.QWEN2_5_7B_INSTRUCT}")
```

!!! info "É um ponto de partida, não uma lista fechada"
    Os construtores continuam aceitando `str`. O enum cobre os modelos
    que o SDK exercita e documenta; qualquer id do Hub segue válido.

Um enum por tarefa:

| Enum | Alimenta |
| --- | --- |
| `TextModel` | `TextGenerator`, `Agent`, `AIChatPipeline` |
| `EmbeddingModel` | `Embedder`, `Retriever`, `HybridRetriever` |
| `RerankerModel` | `Reranker` |
| `VisionModel` | `VisionTextGenerator`, `describe_image_tool` |
| `ImageModel` | `ImageGenerator`, `generate_image_tool` |
| `SpeechToTextModel` | `SpeechToText`, `transcribe_audio_tool` |
| `TextToSpeechModel` | `TextToSpeech`, `speak_tool` |

## Texto e agentes — `TextModel`

| Membro | Params | VRAM (bf16) | Use quando |
| --- | --- | --- | --- |
| `QWEN2_5_0_5B_INSTRUCT` | 0.5B | ~1 GB | CI, testes, CPU de notebook. Responde, mas não raciocina. |
| `QWEN2_5_1_5B_INSTRUCT` | 1.5B | ~3 GB | O menor tamanho que segue prompt de tool calling com consistência. |
| `QWEN2_5_3B_INSTRUCT` | 3B | ~6 GB | GPU pequena, ou CPU com paciência. |
| `QWEN2_5_7B_INSTRUCT` | 7B | ~15 GB | **O padrão para trabalho real.** Cabe em 8 GB com int8. |
| `QWEN2_5_14B_INSTRUCT` | 14B | ~28 GB | 24 GB de VRAM; nítido em objetivos de vários passos. |
| `QWEN2_5_CODER_7B_INSTRUCT` | 7B | ~15 GB | Gerar e revisar código. |
| `PHI_3_5_MINI_INSTRUCT` | 3.8B | ~8 GB | Melhor raciocínio por gigabyte; licença MIT. |
| `MISTRAL_7B_INSTRUCT_V03` | 7B | ~15 GB | Multilíngue europeu, Apache-2.0. |
| `LLAMA_3_1_8B_INSTRUCT` | 8B | ~16 GB | Gated: exige aceitar a licença e token do Hub. |

!!! info "O peso baixa uma vez — depois é cache em disco"
    A primeira chamada escreve os GB em `$HF_HOME/hub` (ou
    `~/.cache/huggingface/hub`); as execuções seguintes leem de lá, sem rede.
    Num **container sem volume** isso se perde a cada restart. Como apontar o
    cache, fixar a revisão, pré-baixar no deploy e rodar offline está em
    **[Pesos de modelos »](model-weights.md#onde-os-pesos-ficam-e-por-que-a-2a-execucao-e-instantanea)**.

!!! tip "Não decore VRAM — pergunte"
    `recommend()` mede a máquina e escolhe a precisão que cabe, de bf16
    até int4:

    ```python
    from tempest_fastapi_sdk.genai import TextModel, recommend

    report = recommend(model_id=TextModel.QWEN2_5_7B_INSTRUCT)
    print(report.dtype, report.fits, report.device)
    ```

!!! warning "Agente precisa de tool calling"
    Um `Agent` com ferramentas só funciona se o backend expõe
    `chat_with_tools`. Modelos abaixo de ~1.5B costumam ignorar o schema
    e responder em texto — o agente vira um respondedor de tiro único.

## Embeddings e RAG — `EmbeddingModel`

| Membro | Dim | Idiomas | Use quando |
| --- | --- | --- | --- |
| `ALL_MINILM_L6_V2` | 384 | inglês | Corpus em inglês; o mais rápido, padrão do RAG. |
| `PARAPHRASE_MULTILINGUAL_MINILM_L12_V2` | 384 | 50+ | PT-BR barato, roda em CPU. |
| `MULTILINGUAL_E5_LARGE` | 1024 | 100+ | Melhor qualidade em PT-BR. |
| `BGE_M3` | 1024 | 100+ | Documento longo (8k tokens), denso + esparso no mesmo modelo. |

!!! danger "`multilingual-e5` exige prefixo"
    O E5 foi treinado com `query: ` na pergunta e `passage: ` no trecho.
    Sem os prefixos a qualidade cai para o patamar do MiniLM — ou seja,
    você paga 1024 dimensões e recebe 384.

Trocar de embedder **invalida o índice**: os vetores gravados vieram do
modelo antigo. Reindexe o corpus inteiro ao mudar esta linha.

## Rerank — `RerankerModel`

| Membro | Use quando |
| --- | --- |
| `MS_MARCO_MINILM_L6_V2` | Inglês; pequeno e rápido, a escolha usual. |
| `BGE_RERANKER_V2_M3` | PT-BR e multilíngue; mais pesado e bem melhor. |

O reranker é **segunda etapa**: o store devolve N candidatos baratos e o
cross-encoder reordena para `top_k`. Ele lê query e trecho juntos, então
custa caro por par — não é substituto do embedder, é o filtro fino
depois dele.

## Visão — `VisionModel`

| Membro | Params | Use quando |
| --- | --- | --- |
| `QWEN2_VL_2B_INSTRUCT` | 2B | Legenda, leitura de tela, agente conferindo o que desenhou. |
| `QWEN2_VL_7B_INSTRUCT` | 7B | Pergunta e resposta visual de verdade; 16 GB. |
| `LLAVA_1_5_7B` | 7B | A referência clássica, com muito material publicado. |

## Imagem — `ImageModel`

| Membro | Passos | Use quando |
| --- | --- | --- |
| `SDXL_TURBO` | ~4 | Preview rápido, laço de agente, tolerável em CPU. |
| `SDXL_BASE_1_0` | ~30 | Qualidade final em 1024², 10+ GB de VRAM. |
| `FLUX_1_SCHNELL` | ~4 | Melhor aderência ao prompt entre os rápidos; 16+ GB. |

!!! warning "Passo errado custa 10x"
    Um checkpoint turbo quer ~4 passos de difusão e um completo quer ~30.
    Se o LLM escolher às cegas, uma renderização demora dez vezes mais
    que o necessário — fixe no `default_steps` da ferramenta:

    ```python
    from tempest_fastapi_sdk.agents import generate_image_tool
    from tempest_fastapi_sdk.genai import ImageGenerator, ImageModel

    tool = generate_image_tool(
        ImageGenerator(ImageModel.SDXL_TURBO),
        default_steps=4,
    )
    ```

## Áudio — `SpeechToTextModel` e `TextToSpeechModel`

| Membro (STT) | Use quando |
| --- | --- |
| `TINY` | Teste de fumaça; erra sotaque e número. |
| `BASE` | O padrão — PT-BR utilizável em CPU. |
| `SMALL` | Áudio com ruído. |
| `MEDIUM` | Entrevista, ligação — quando a transcrição é o produto. |
| `LARGE_V3` | Melhor precisão; GPU, ou muita paciência. |

| Membro (TTS) | Use quando |
| --- | --- |
| `XTTS_V2` | Multilíngue + clonagem de voz. O padrão, e o mais pesado. |
| `VITS_PT_BR` | Só PT-BR, rápido, sem clonagem. |
| `VITS_EN` | Só inglês, rápido. |

## Um exemplo fechando tudo

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, describe_image_tool, generate_image_tool
from tempest_fastapi_sdk.genai import (
    ImageGenerator,
    ImageModel,
    TextGenerator,
    TextModel,
    VisionModel,
    VisionTextGenerator,
)

agent = Agent(
    TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT),
    tools=[
        generate_image_tool(
            ImageGenerator(ImageModel.SDXL_TURBO),
            default_steps=4,
        ),
        describe_image_tool(VisionTextGenerator(VisionModel.QWEN2_VL_2B_INSTRUCT)),
    ],
)


async def main() -> None:
    """Draw something, then ask the vision model what came out."""
    run = await agent.run(
        "Desenhe uma bicicleta vermelha como bike.png e depois descreva a imagem.",
    )
    print(run.output)


asyncio.run(main())
```

## Recapitulando

* O id continua sendo `str` — o enum só dá nome ao que o SDK exercita.
* `TextModel.QWEN2_5_7B_INSTRUCT` é o ponto de partida para trabalho
  real; `QWEN2_5_0_5B_INSTRUCT` é para teste, não para qualidade.
* Trocar `EmbeddingModel` obriga a reindexar o corpus.
* Checkpoint de imagem turbo quer ~4 passos; completo, ~30.
* Antes de baixar 15 GB, pergunte ao `recommend()` se cabe.
